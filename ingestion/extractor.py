"""
ingestion/extractor.py
Épico 1 — Captação e Limpeza de Projetos de Lei da Câmara dos Deputados.

Fonte: https://dadosabertos.camara.leg.br/api/v2/proposicoes
Params fixos: siglaTipo=PL, ordem=DESC, ordenarPor=id

Responsabilidades:
  - Buscar PLs da API pública da Câmara com paginação real (campo 'pagina')
  - Parar a varredura ao encontrar o primeiro ID já presente no banco
  - Respeitar rate limiting com delay configurável entre páginas
  - Sanitizar o texto bruto (HTML, UTF-8, espaços)
  - Validar: texto < 50 chars após limpeza → status ERRO_CAPTURA
  - Persistir com status AGUARDANDO_IA (pronto para processamento LLM)
"""

import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Generator

import requests
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from models.database import get_session, check_connection
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

# Mínimo de caracteres válidos após sanitização; abaixo disso → ERRO_CAPTURA
_MIN_TEXT_LENGTH = 50


# =============================================================================
# Configuração centralizada
# =============================================================================
@dataclass(frozen=True)
class ExtractorConfig:
    """Parâmetros de configuração do extrator, carregados do arquivo .env."""

    base_url: str = field(
        default_factory=lambda: os.getenv(
            "API_BASE_URL", "https://dadosabertos.camara.leg.br/api/v2"
        )
    )
    api_key: str = field(
        default_factory=lambda: os.getenv("API_KEY", "")
    )
    timeout: int = field(
        default_factory=lambda: int(os.getenv("API_TIMEOUT_SECONDS", "15"))
    )
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("API_MAX_RETRIES", "3"))
    )
    retry_backoff: float = field(
        default_factory=lambda: float(os.getenv("API_RETRY_BACKOFF_SECONDS", "2"))
    )
    max_pages: int = field(
        default_factory=lambda: int(os.getenv("MAX_PAGES_PER_RUN", "3"))
    )
    page_size: int = field(
        default_factory=lambda: int(os.getenv("API_PAGE_SIZE", "100"))
    )
    page_delay: float = field(
        default_factory=lambda: float(os.getenv("API_PAGE_DELAY_SECONDS", "1.0"))
    )
    early_stop_on_duplicate: bool = field(
        default_factory=lambda: os.getenv("EXTRACTOR_EARLY_STOP_ON_DUPLICATE", "true").lower() == "true"
    )


# =============================================================================
# Sanitização — funções puras, sem dependências externas
# =============================================================================

_RE_HTML_TAGS     = re.compile(r"<[^>]+>", re.IGNORECASE)
_RE_HTML_ENTITIES = re.compile(r"&[a-zA-Z]{2,6};|&#\d{1,4};|&#x[0-9a-fA-F]{1,4};")
_RE_WHITESPACE    = re.compile(r"[ \t]+")
_RE_NEWLINES      = re.compile(r"\n{3,}")
_RE_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(raw_text: str) -> str:
    """
    Limpa e normaliza o texto bruto recebido da API.

    Etapas:
      1. Força encoding UTF-8 (descarta bytes inválidos)
      2. Remove tags HTML (<p>, <b>, <br/>, etc.)
      3. Remove entidades HTML (&amp;, &nbsp;, &#160;, etc.)
      4. Remove caracteres de controle ASCII não-imprimíveis
      5. Normalização Unicode NFC
      6. Colapsa múltiplos espaços/tabs em espaço único
      7. Reduz quebras de linha > 2 consecutivas para exatamente 2
      8. Trim final
    """
    if not raw_text:
        return ""

    txt = raw_text.encode("utf-8", errors="ignore").decode("utf-8")
    txt = _RE_HTML_TAGS.sub(" ", txt)
    txt = _RE_HTML_ENTITIES.sub(" ", txt)
    txt = _RE_CONTROL_CHARS.sub("", txt)
    txt = unicodedata.normalize("NFC", txt)
    txt = _RE_WHITESPACE.sub(" ", txt)
    txt = _RE_NEWLINES.sub("\n\n", txt)
    return txt.strip()


# =============================================================================
# Cliente HTTP — retry com backoff exponencial
# =============================================================================

class ApiClient:
    """
    Cliente HTTP resiliente para a API dadosabertos.camara.leg.br.

    Retry automático com backoff exponencial em:
      - Erros de servidor 5xx
      - Timeout de conexão / leitura
      - ConnectionError (rede instável)

    Sem retry em 4xx (erros definitivos do cliente).
    A API da Câmara não exige autenticação; API_KEY fica vazia por padrão.
    """

    def __init__(self, config: ExtractorConfig) -> None:
        self._config = config
        self._session = self._build_session()

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        headers = {
            "Accept": "application/json",
            "User-Agent": "PautaLimpa-Bot/1.0 (contato@pautalimpa.com.br)",
        }
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        session.headers.update(headers)
        return session

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict:
        """
        Executa GET e retorna o dict completo da resposta JSON.

        A API da Câmara retorna:
          {"dados": [...], "links": [{"rel": "next", "href": "..."}, ...]}

        Returns:
            dict: Resposta completa com 'dados' e 'links'.

        Raises:
            RuntimeError: Se todos os retries falharem.
        """
        url = f"{self._config.base_url}{endpoint}"
        last_exception: Exception | None = None

        for attempt in range(1, self._config.max_retries + 1):
            try:
                logger.debug(
                    "GET %s | params=%s | tentativa %d/%d",
                    url, params, attempt, self._config.max_retries,
                )
                response = self._session.get(
                    url, params=params, timeout=self._config.timeout
                )

                if 400 <= response.status_code < 500:
                    logger.error("Erro cliente [%d] em %s — sem retry.", response.status_code, url)
                    response.raise_for_status()

                if response.status_code >= 500:
                    wait = self._config.retry_backoff * (2 ** (attempt - 1))
                    logger.warning(
                        "Erro servidor [%d] em %s | aguardando %.1fs (tentativa %d/%d).",
                        response.status_code, url, wait, attempt, self._config.max_retries,
                    )
                    time.sleep(wait)
                    continue

                data = response.json()
                return data if isinstance(data, dict) else {"dados": data, "links": []}

            except requests.Timeout as exc:
                wait = self._config.retry_backoff * (2 ** (attempt - 1))
                last_exception = exc
                logger.warning("Timeout tentativa %d/%d para %s | aguardando %.1fs.", attempt, self._config.max_retries, url, wait)
                time.sleep(wait)

            except requests.ConnectionError as exc:
                wait = self._config.retry_backoff * (2 ** (attempt - 1))
                last_exception = exc
                logger.warning("ConnectionError tentativa %d/%d para %s | aguardando %.1fs.", attempt, self._config.max_retries, url, wait)
                time.sleep(wait)

        logger.error("Todas as %d tentativas esgotadas para %s.", self._config.max_retries, url)
        raise last_exception or RuntimeError(f"Falha ao acessar {url} após {self._config.max_retries} tentativas.")


# =============================================================================
# Repositório — persistência na tabela projetos_brutos
# =============================================================================

class ProjetoRepository:
    """Persistência de projetos brutos; desacoplado do extrator."""

    _INSERT_SQL = text("""
        INSERT INTO projetos_brutos (
            id_origem, sigla_tipo, numero, ano,
            ementa_bruta, uri_camara, link_oficial,
            data_apresentacao, status_processamento
        ) VALUES (
            :id_origem, :sigla_tipo, :numero, :ano,
            :ementa_bruta, :uri_camara, :link_oficial,
            :data_apresentacao, :status_processamento
        )
        ON CONFLICT ON CONSTRAINT uq_projeto_id_origem DO NOTHING
    """)

    _EXISTS_SQL = text("""
        SELECT 1 FROM projetos_brutos WHERE id_origem = :id_origem LIMIT 1
    """)

    def exists(self, id_origem: str) -> bool:
        """Verifica se um projeto já existe no banco pelo ID de origem."""
        with get_session() as session:
            result = session.execute(self._EXISTS_SQL, {"id_origem": id_origem})
            return result.fetchone() is not None

    def save(self, projeto: dict) -> bool:
        """
        Insere um projeto bruto. Retorna True se inserido, False se duplicata.
        Status inicial: AGUARDANDO_IA (texto já sanitizado inline).
        Se texto_sanitizado < 50 chars, status → ERRO_CAPTURA.
        """
        with get_session() as session:
            try:
                result = session.execute(self._INSERT_SQL, projeto)
                session.commit()
                return result.rowcount > 0
            except IntegrityError:
                session.rollback()
                logger.warning("Duplicata ignorada | id_origem=%s", projeto.get("id_origem"))
                return False
            except SQLAlchemyError as exc:
                session.rollback()
                logger.error("Erro DB ao salvar id_origem=%s: %s", projeto.get("id_origem"), exc)
                raise


# =============================================================================
# Extrator principal — API dadosabertos.camara.leg.br
# =============================================================================

class LegislativeExtractor:
    """
    Épico 1: Captação de PLs da API pública da Câmara dos Deputados.

    Fluxo:
      1. Busca paginada com siglaTipo=PL, ordem=DESC, ordenarPor=id
      2. Para cada item: sanitiza texto → valida tamanho → persiste
      3. Early-stop: ao encontrar o primeiro ID duplicado (já no banco),
         interrompe a varredura (ordenação DESC garante que tudo
         abaixo também já foi capturado anteriormente)
      4. Respeita delay entre páginas para não sobrecarregar o servidor
    """

    _ENDPOINT  = "/proposicoes"
    _BASE_PARAMS = {
        "siglaTipo":  "PL",
        "ordem":      "DESC",
        "ordenarPor": "id",
    }

    def __init__(
        self,
        config:     ExtractorConfig   | None = None,
        client:     ApiClient         | None = None,
        repository: ProjetoRepository | None = None,
    ) -> None:
        self._config = config or ExtractorConfig()
        self._client = client or ApiClient(self._config)
        self._repo   = repository or ProjetoRepository()

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _has_next_page(self, links: list[dict]) -> bool:
        """Verifica se a resposta da API indica uma próxima página."""
        return any(link.get("rel") == "next" for link in links)

    def _map_to_projeto(self, item: dict) -> dict:
        """
        Mapeia um item da API da Câmara para o schema de projetos_brutos.

        Campos da API /proposicoes:
          id, uri, siglaTipo, codTipo, numero, ano, ementa
        """
        ementa_sanitizada = sanitize_text(item.get("ementa", ""))

        # Valida comprimento mínimo do texto; abaixo do limiar → ERRO_CAPTURA
        if len(ementa_sanitizada) < _MIN_TEXT_LENGTH:
            logger.warning(
                "Texto insuficiente após sanitização (%d chars) para id_origem=%s — marcando ERRO_CAPTURA.",
                len(ementa_sanitizada),
                item.get("id"),
            )
            status = "ERRO_CAPTURA"
        else:
            status = "AGUARDANDO_IA"

        return {
            "id_origem":            str(item["id"]),
            "sigla_tipo":           item.get("siglaTipo", "PL"),
            "numero":               item.get("numero"),
            "ano":                  item["ano"],
            "ementa_bruta":         ementa_sanitizada,
            "uri_camara":           item.get("uri", ""),
            "link_oficial":         item.get("uri", ""),
            "data_apresentacao":    item.get("dataApresentacao"),
            "status_processamento": status,
        }

    def _fetch_page(self, page: int) -> tuple[list[dict], bool]:
        """
        Busca uma página da API.

        Returns:
            Tupla (itens, tem_proxima_pagina).
        """
        params = {
            **self._BASE_PARAMS,
            "pagina": page,
            "itens":  self._config.page_size,
        }
        logger.info("Buscando página %d (itens=%d)...", page, self._config.page_size)
        response = self._client.get(self._ENDPOINT, params=params)
        items     = response.get("dados", [])
        has_next  = self._has_next_page(response.get("links", []))
        logger.info("Página %d: %d item(s) recebido(s) | próxima=%s", page, len(items), has_next)
        return items, has_next

    def _paginate(self) -> Generator[dict, None, None]:
        """
        Gerador que itera os itens das páginas da API.

        Condições de parada (qualquer uma delas):
          1. Página retorna lista vazia
          2. Limite MAX_PAGES_PER_RUN atingido
          3. Item encontrado já existe no banco (early-stop por duplicata)
          4. API indica que não há próxima página (rel='next' ausente)
        """
        for page in range(1, self._config.max_pages + 1):
            items, has_next = self._fetch_page(page)

            if not items:
                logger.info("Página %d vazia — fim da paginação.", page)
                return

            for item in items:
                id_origem = str(item.get("id", ""))

                # Early-stop: primeiro duplicado indica que todos os
                # registros subsequentes (ordem DESC) já estão no banco
                if self._config.early_stop_on_duplicate and self._repo.exists(id_origem):
                    logger.info(
                        "ID %s já existe no banco — interrompendo paginação (early-stop).",
                        id_origem,
                    )
                    return

                yield item

            if not has_next:
                logger.info("API indicou última página — paginação encerrada.")
                return

            # Delay entre páginas para respeitar rate limit do servidor
            if page < self._config.max_pages:
                logger.debug("Aguardando %.1fs antes da próxima página...", self._config.page_delay)
                time.sleep(self._config.page_delay)

    # ------------------------------------------------------------------
    # Ponto de entrada público
    # ------------------------------------------------------------------

    def run(self) -> dict[str, int]:
        """
        Executa o ciclo completo de extração.

        Returns:
            dict: {"total_recebidos", "inseridos", "erro_captura", "duplicatas", "erros"}
        """
        logger.info("=" * 65)
        logger.info("PautaLimpa | Extração — API Câmara dos Deputados")
        logger.info("Fonte: %s%s", self._config.base_url, self._ENDPOINT)
        logger.info("Params: siglaTipo=PL | ordem=DESC | ordenarPor=id | máx. páginas=%d", self._config.max_pages)
        logger.info("=" * 65)

        stats = {
            "total_recebidos": 0,
            "inseridos":       0,
            "erro_captura":    0,
            "duplicatas":      0,
            "erros":           0,
        }

        for item in self._paginate():
            stats["total_recebidos"] += 1
            try:
                projeto  = self._map_to_projeto(item)
                inserido = self._repo.save(projeto)

                if inserido:
                    if projeto["status_processamento"] == "ERRO_CAPTURA":
                        stats["erro_captura"] += 1
                        logger.warning(
                            "ERRO_CAPTURA | id_origem=%s | %s %s/%s",
                            projeto["id_origem"], projeto["sigla_tipo"],
                            projeto.get("numero"), projeto["ano"],
                        )
                    else:
                        stats["inseridos"] += 1
                        logger.info(
                            "INSERIDO | id_origem=%s | %s %s/%s | %d chars",
                            projeto["id_origem"], projeto["sigla_tipo"],
                            projeto.get("numero"), projeto["ano"],
                            len(projeto["ementa_bruta"]),
                        )
                else:
                    stats["duplicatas"] += 1

            except Exception as exc:  # pylint: disable=broad-except
                stats["erros"] += 1
                logger.error(
                    "ERRO ao processar item id=%s: %s",
                    item.get("id", "?"), exc, exc_info=True,
                )

        logger.info("=" * 65)
        logger.info(
            "Extração concluída | recebidos=%d | inseridos=%d | erro_captura=%d | duplicatas=%d | erros=%d",
            stats["total_recebidos"], stats["inseridos"],
            stats["erro_captura"], stats["duplicatas"], stats["erros"],
        )
        logger.info("=" * 65)
        return stats


# =============================================================================
# Entrypoint
# =============================================================================

if __name__ == "__main__":
    if not check_connection():
        logger.critical(
            "Conexão com o banco falhou. Verifique as configurações no .env."
        )
        raise SystemExit(1)

    extractor = LegislativeExtractor()
    resultado = extractor.run()
    raise SystemExit(0 if resultado["erros"] == 0 else 1)

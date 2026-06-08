"""
publishing/image_generator.py
Épico 4 — Geração Automatizada de Criativo via API Placid.

Responsabilidades:
  - Enviar título simplificado para a API Placid (template 1080x1350px)
  - Alternar entre templates para A/B testing (Épico 8)
  - Retornar URL pública temporária da imagem gerada
  - Atualizar publicacoes_instagram com url_imagem e template_utilizado
"""

import os
import time
import itertools

import requests
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from models.database import get_session
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

# Placid API
_PLACID_API_URL = "https://api.placid.app/api/rest"
_PLACID_TIMEOUT = int(os.getenv("PLACID_TIMEOUT_SECONDS", "30"))

# Templates cadastrados no Placid (IDs separados por vírgula no .env)
# Alternância cíclica para A/B testing (Épico 8)
_TEMPLATES_RAW = os.getenv("PLACID_TEMPLATE_IDS", "")
_TEMPLATES: list[str] = [t.strip() for t in _TEMPLATES_RAW.split(",") if t.strip()]
_template_cycle = itertools.cycle(_TEMPLATES) if _TEMPLATES else None

# SQLs
_UPSERT_PUBLICACAO_SQL = text("""
    INSERT INTO publicacoes_instagram (fk_projeto, url_imagem, template_utilizado, caption_usada, status)
    VALUES (:fk_projeto, :url_imagem, :template_utilizado, :caption_usada, 'AGUARDANDO')
    ON CONFLICT ON CONSTRAINT uq_publicacao_projeto
    DO UPDATE SET
        url_imagem         = EXCLUDED.url_imagem,
        template_utilizado = EXCLUDED.template_utilizado,
        caption_usada      = EXCLUDED.caption_usada
""")

_UPDATE_PROJETO_STATUS_SQL = text("""
    UPDATE projetos_brutos
    SET status_processamento = 'AGUARDANDO_PUBLICACAO'
    WHERE id = :id
""")

_FETCH_MIDIA_QUEUE_SQL = text("""
    SELECT
        pb.id          AS projeto_id,
        pb.sigla_tipo,
        pb.numero,
        pb.ano,
        COALESCE(ai.texto_traduzido, pia.texto_traduzido) AS caption
    FROM projetos_brutos pb
    LEFT JOIN analises_ia ai
        ON ai.fk_projeto = pb.id
       AND ai.tipo_analise = 'IMPARCIAL'
    LEFT JOIN processamento_ia pia
        ON pia.fk_projeto = pb.id
       AND ai.id IS NULL
    WHERE pb.status_processamento = 'AGUARDANDO_MIDIA'
    ORDER BY pb.data_captura ASC
    LIMIT :limit
""")


class ImageGenerator:
    """
    Gera criativos para publicação no Instagram via API Placid.

    O template deve estar configurado no painel do Placid com:
      - Dimensões: 1080px × 1350px (formato retrato do Instagram)
      - Variável de texto: "titulo" (campo enviado via API)
    """

    def __init__(self) -> None:
        self._api_key = os.getenv("PLACID_API_KEY", "")
        if not self._api_key:
            logger.warning(
                "PLACID_API_KEY não configurada. "
                "Geração de imagens funcionará em modo simulado."
            )

    def _next_template(self) -> str:
        """Retorna o próximo template ID em modo cíclico (A/B testing)."""
        if _template_cycle:
            return next(_template_cycle)
        return os.getenv("PLACID_TEMPLATE_IDS", "default_template")

    def generate(self, titulo: str, caption: str) -> tuple[str, str]:
        """
        Solicita a geração da imagem via API Placid.

        Args:
            titulo:  Título simplificado da lei (ex: "PL - 1234/2024").
            caption: Caption completo para referência/A/B testing.

        Returns:
            tuple[str, str]: (url_publica_imagem, template_id_usado)
        """
        template_id = self._next_template()

        if not self._api_key or template_id == "default_template":
            logger.warning("Modo simulado: retornando URL de imagem placeholder.")
            return (
                "https://via.placeholder.com/1080x1350.jpg?text=PautaLimpa",
                template_id,
            )

        payload = {
            "template_uuid": template_id,
            "layers": {
                "titulo": {"text": titulo[:100]},
            },
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type":  "application/json",
        }

        for attempt in range(1, 4):
            try:
                response = requests.post(
                    f"{_PLACID_API_URL}/images",
                    json=payload,
                    headers=headers,
                    timeout=_PLACID_TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()

                # Aguarda processamento assíncrono se necessário (status "queued")
                image_url = data.get("image_url") or data.get("pdf_url") or ""
                status    = data.get("status", "finished")

                if status == "queued":
                    poll_url = data.get("polling_url", "")
                    image_url = self._poll_until_ready(poll_url, headers)

                logger.info("Imagem gerada | template=%s | url=%s", template_id, image_url)
                return image_url, template_id

            except requests.HTTPError as exc:
                logger.warning("Placid HTTP erro tentativa %d/3: %s", attempt, exc)
                time.sleep(5 * attempt)
            except requests.RequestException as exc:
                logger.warning("Placid conexão erro tentativa %d/3: %s", attempt, exc)
                time.sleep(5 * attempt)

        raise RuntimeError(f"Falha ao gerar imagem via Placid após 3 tentativas. Template={template_id}")

    def _poll_until_ready(self, poll_url: str, headers: dict, max_wait: int = 60) -> str:
        """Polling na API Placid até a imagem estar pronta."""
        elapsed = 0
        while elapsed < max_wait:
            time.sleep(3)
            elapsed += 3
            resp = requests.get(poll_url, headers=headers, timeout=15)
            data = resp.json()
            if data.get("status") == "finished":
                return data.get("image_url", "")
        raise TimeoutError(f"Placid não concluiu a geração em {max_wait}s.")

    def process_queue(self, batch_size: int = 10) -> dict[str, int]:
        """
        Processa todos os projetos em AGUARDANDO_MIDIA.

        Returns:
            dict: {"processados": N, "sucesso": N, "erro": N}
        """
        stats = {"processados": 0, "sucesso": 0, "erro": 0}

        with get_session() as session:
            rows = session.execute(_FETCH_MIDIA_QUEUE_SQL, {"limit": batch_size}).fetchall()

        for row in rows:
            stats["processados"] += 1
            try:
                titulo  = f"{row.sigla_tipo} - {row.numero}/{row.ano}"
                caption = row.caption or titulo

                url_imagem, template_id = self.generate(titulo, caption)

                with get_session() as session:
                    session.execute(_UPSERT_PUBLICACAO_SQL, {
                        "fk_projeto":        row.projeto_id,
                        "url_imagem":        url_imagem,
                        "template_utilizado": template_id,
                        "caption_usada":     caption,
                    })
                    session.execute(_UPDATE_PROJETO_STATUS_SQL, {"id": row.projeto_id})
                    session.commit()

                stats["sucesso"] += 1
                logger.info(
                    "Mídia gerada | id=%d | %s | template=%s",
                    row.projeto_id, titulo, template_id,
                )

            except Exception as exc:  # pylint: disable=broad-except
                stats["erro"] += 1
                logger.error("Erro ao gerar mídia para id=%d: %s", row.projeto_id, exc, exc_info=True)

        logger.info(
            "Geração de mídia concluída | processados=%d | sucesso=%d | erro=%d",
            stats["processados"], stats["sucesso"], stats["erro"],
        )
        return stats

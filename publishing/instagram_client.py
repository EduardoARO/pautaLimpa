"""
publishing/instagram_client.py
Épico 4 — Publicação no Instagram via Meta Graph API.

Fluxo obrigatório (2 chamadas sequenciais):
  1. POST /{ig_user_id}/media           → cria container, retorna creation_id
  2. POST /{ig_user_id}/media_publish   → publica o container, retorna media_id

Épico 7 — Retroalimentação de falhas:
  - Erros transientes (5xx/timeout): volta para fila com contador de tentativas
  - Após 3 tentativas: FALHA_CRITICA + notificação à equipe

Épico 7 — Coleta de métricas:
  - método collect_insights(): consulta /{media_id}/insights 24h após publicação
"""

import os
import time

import requests
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from models.database import get_session
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

_GRAPH_API_BASE    = "https://graph.facebook.com/v19.0"
_MAX_PUBLISH_RETRIES = 3
_RETRY_WAIT_SECONDS  = 30

# SQLs
_FETCH_PUBLISH_QUEUE_SQL = text("""
    SELECT
        pb.id           AS projeto_id,
        pb.tentativas_publicacao,
        pi.id           AS publicacao_id,
        pi.url_imagem,
        pi.caption_usada
    FROM projetos_brutos pb
    JOIN publicacoes_instagram pi ON pi.fk_projeto = pb.id
    WHERE pb.status_processamento = 'AGUARDANDO_PUBLICACAO'
      AND pi.status = 'AGUARDANDO'
    ORDER BY pb.data_captura ASC
    LIMIT :limit
""")

_UPDATE_PUBLICACAO_SQL = text("""
    UPDATE publicacoes_instagram
    SET container_id        = :container_id,
        media_id            = :media_id,
        status              = :status,
        data_publicacao     = CASE WHEN :status = 'PUBLICADO' THEN NOW() ELSE data_publicacao END,
        data_delecao_imagem = CASE WHEN :status = 'PUBLICADO' THEN NOW() + INTERVAL '24 hours' ELSE data_delecao_imagem END
    WHERE id = :id
""")

_UPDATE_PROJETO_STATUS_SQL = text("""
    UPDATE projetos_brutos
    SET status_processamento  = :status,
        tentativas_publicacao = tentativas_publicacao + 1
    WHERE id = :id
""")

_INSERT_LOG_SQL = text("""
    INSERT INTO logs_publicacao (fk_projeto, tentativa, status, mensagem, payload_enviado, resposta_api)
    VALUES (:fk_projeto, :tentativa, :status, :mensagem, :payload_enviado, :resposta_api)
""")

_INSERT_METRICAS_SQL = text("""
    INSERT INTO metricas_engajamento
        (fk_publicacao, curtidas, comentarios, compartilhamentos, salvamentos, alcance, impressoes)
    VALUES
        (:fk_publicacao, :curtidas, :comentarios, :compartilhamentos, :salvamentos, :alcance, :impressoes)
""")

_FETCH_INSIGHTS_CANDIDATES_SQL = text("""
    SELECT pi.id, pi.media_id, pi.fk_projeto
    FROM publicacoes_instagram pi
    LEFT JOIN metricas_engajamento me ON me.fk_publicacao = pi.id
    WHERE pi.status = 'PUBLICADO'
      AND pi.data_publicacao <= NOW() - INTERVAL '24 hours'
      AND me.id IS NULL
    LIMIT 20
""")


class InstagramClient:
    """
    Publica conteúdo no Instagram via Meta Graph API (2 etapas).
    Gerencia retentativas e retroalimentação de falhas transientes.
    """

    def __init__(self) -> None:
        self._access_token = os.getenv("META_ACCESS_TOKEN", "")
        self._ig_user_id   = os.getenv("META_IG_USER_ID", "")
        if not self._access_token or not self._ig_user_id:
            logger.warning(
                "META_ACCESS_TOKEN ou META_IG_USER_ID não configurados. "
                "Publicação funcionará em modo simulado."
            )

    def _post(self, endpoint: str, payload: dict) -> dict:
        """Executa POST na Graph API com retry para 5xx."""
        url = f"{_GRAPH_API_BASE}/{endpoint}"
        payload["access_token"] = self._access_token

        for attempt in range(1, _MAX_PUBLISH_RETRIES + 1):
            try:
                response = requests.post(url, data=payload, timeout=30)
                if response.status_code >= 500:
                    logger.warning(
                        "Graph API 5xx (tentativa %d/%d) | aguardando %ds.",
                        attempt, _MAX_PUBLISH_RETRIES, _RETRY_WAIT_SECONDS,
                    )
                    time.sleep(_RETRY_WAIT_SECONDS)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.Timeout:
                logger.warning("Timeout Graph API tentativa %d/%d.", attempt, _MAX_PUBLISH_RETRIES)
                time.sleep(_RETRY_WAIT_SECONDS)
            except requests.ConnectionError:
                logger.warning("ConnectionError Graph API tentativa %d/%d.", attempt, _MAX_PUBLISH_RETRIES)
                time.sleep(_RETRY_WAIT_SECONDS)

        raise RuntimeError(f"Graph API falhou após {_MAX_PUBLISH_RETRIES} tentativas em {endpoint}")

    def publish(self, url_imagem: str, caption: str) -> tuple[str, str]:
        """
        Executa o fluxo completo de publicação (container → publish).

        Args:
            url_imagem: URL pública acessível da imagem (1080x1350px).
            caption:    Legenda da publicação (máx. 2.200 chars).

        Returns:
            tuple[str, str]: (container_id, media_id)
        """
        if not self._access_token:
            logger.warning("Modo simulado: retornando IDs fictícios.")
            return ("SIMULATED_CONTAINER_ID", "SIMULATED_MEDIA_ID")

        # Etapa 1: Criar container de mídia
        logger.info("Graph API: criando container de mídia...")
        container_resp = self._post(
            f"{self._ig_user_id}/media",
            {"image_url": url_imagem, "caption": caption[:2200]},
        )
        container_id = container_resp.get("id", "")
        if not container_id:
            raise ValueError(f"Container ID não retornado pela Graph API: {container_resp}")

        logger.info("Container criado: %s", container_id)

        # Aguarda o container ser processado pela Meta (até 30s)
        time.sleep(5)

        # Etapa 2: Publicar o container
        logger.info("Graph API: publicando container %s...", container_id)
        publish_resp = self._post(
            f"{self._ig_user_id}/media_publish",
            {"creation_id": container_id},
        )
        media_id = publish_resp.get("id", "")
        if not media_id:
            raise ValueError(f"Media ID não retornado pela Graph API: {publish_resp}")

        logger.info("Publicado com sucesso! Media ID: %s", media_id)
        return container_id, media_id

    def _log_attempt(
        self,
        session,
        fk_projeto: int,
        tentativa: int,
        status: str,
        mensagem: str,
        payload: dict,
        resposta: dict,
    ) -> None:
        """Grava log imutável de tentativa de publicação."""
        import json
        session.execute(_INSERT_LOG_SQL, {
            "fk_projeto":      fk_projeto,
            "tentativa":       tentativa,
            "status":          status,
            "mensagem":        mensagem,
            "payload_enviado": json.dumps(payload),
            "resposta_api":    json.dumps(resposta),
        })

    def process_queue(self, batch_size: int = 3) -> dict[str, int]:
        """
        Publica até batch_size projetos em AGUARDANDO_PUBLICACAO.
        Padrão de 3 leis/dia conforme Épico 3 — estratégia de conteúdo.

        Returns:
            dict: {"processados": N, "publicados": N, "falha_transiente": N, "falha_critica": N}
        """
        stats = {"processados": 0, "publicados": 0, "falha_transiente": 0, "falha_critica": 0}

        with get_session() as session:
            rows = session.execute(_FETCH_PUBLISH_QUEUE_SQL, {"limit": batch_size}).fetchall()

        if not rows:
            logger.info("Fila de publicação vazia — nada a publicar.")
            return stats

        for row in rows:
            stats["processados"] += 1
            tentativa = row.tentativas_publicacao + 1

            try:
                container_id, media_id = self.publish(row.url_imagem, row.caption_usada)

                with get_session() as session:
                    session.execute(_UPDATE_PUBLICACAO_SQL, {
                        "id":           row.publicacao_id,
                        "container_id": container_id,
                        "media_id":     media_id,
                        "status":       "PUBLICADO",
                    })
                    session.execute(_UPDATE_PROJETO_STATUS_SQL, {
                        "id":     row.projeto_id,
                        "status": "POSTADO",
                    })
                    self._log_attempt(
                        session, row.projeto_id, tentativa,
                        "200", "Publicado com sucesso",
                        {"url_imagem": row.url_imagem},
                        {"media_id": media_id},
                    )
                    session.commit()

                stats["publicados"] += 1
                logger.info(
                    "POSTADO | id=%d | media_id=%s | tentativa=%d",
                    row.projeto_id, media_id, tentativa,
                )

            except Exception as exc:  # pylint: disable=broad-except
                # Distingue erro transiente de erro definitivo
                is_definitive = any(
                    keyword in str(exc).lower()
                    for keyword in ["token", "expired", "blocked", "invalid_token", "oauth"]
                )

                if is_definitive or tentativa >= _MAX_PUBLISH_RETRIES:
                    novo_status_proj = "FALHA_CRITICA"
                    novo_status_pub  = "FALHA_CRITICA"
                    stats["falha_critica"] += 1
                    logger.error(
                        "FALHA_CRITICA | id=%d | tentativa=%d | %s",
                        row.projeto_id, tentativa, exc,
                    )
                else:
                    novo_status_proj = "AGUARDANDO_PUBLICACAO"
                    novo_status_pub  = "FALHA_TRANSIENTE"
                    stats["falha_transiente"] += 1
                    logger.warning(
                        "FALHA_TRANSIENTE | id=%d | tentativa=%d/%d | %s",
                        row.projeto_id, tentativa, _MAX_PUBLISH_RETRIES, exc,
                    )

                with get_session() as session:
                    session.execute(_UPDATE_PUBLICACAO_SQL, {
                        "id":           row.publicacao_id,
                        "container_id": None,
                        "media_id":     None,
                        "status":       novo_status_pub,
                    })
                    session.execute(_UPDATE_PROJETO_STATUS_SQL, {
                        "id":     row.projeto_id,
                        "status": novo_status_proj,
                    })
                    self._log_attempt(
                        session, row.projeto_id, tentativa,
                        "ERRO", str(exc), {}, {},
                    )
                    session.commit()

        logger.info(
            "Publicação concluída | publicados=%d | transiente=%d | critica=%d",
            stats["publicados"], stats["falha_transiente"], stats["falha_critica"],
        )
        return stats

    def collect_insights(self) -> dict[str, int]:
        """
        Épico 7: Coleta métricas de engajamento de posts publicados há 24h+.

        Returns:
            dict: {"coletados": N, "erros": N}
        """
        stats = {"coletados": 0, "erros": 0}

        with get_session() as session:
            rows = session.execute(_FETCH_INSIGHTS_CANDIDATES_SQL).fetchall()

        for row in rows:
            try:
                metricas = self._fetch_insights(row.media_id)
                with get_session() as session:
                    session.execute(_INSERT_METRICAS_SQL, {
                        "fk_publicacao":    row.id,
                        "curtidas":         metricas.get("like_count", 0),
                        "comentarios":      metricas.get("comments_count", 0),
                        "compartilhamentos": metricas.get("shares", 0),
                        "salvamentos":      metricas.get("saved", 0),
                        "alcance":          metricas.get("reach", 0),
                        "impressoes":       metricas.get("impressions", 0),
                    })
                    session.commit()

                stats["coletados"] += 1
                logger.info("Insights coletados | media_id=%s", row.media_id)

            except Exception as exc:  # pylint: disable=broad-except
                stats["erros"] += 1
                logger.error("Erro ao coletar insights media_id=%s: %s", row.media_id, exc)

        return stats

    def _fetch_insights(self, media_id: str) -> dict:
        """Busca métricas via GET /{media_id}/insights."""
        if not self._access_token or media_id.startswith("SIMULATED"):
            return {}

        # Delay entre chamadas para respeitar rate limit da Meta
        time.sleep(1)

        url = f"{_GRAPH_API_BASE}/{media_id}/insights"
        params = {
            "metric":       "like_count,comments_count,shares,saved,reach,impressions",
            "access_token": self._access_token,
        }
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()

        data   = response.json().get("data", [])
        result = {}
        for item in data:
            result[item["name"]] = item.get("values", [{}])[0].get("value", 0)
        return result

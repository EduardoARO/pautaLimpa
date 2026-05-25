"""
pipeline/orchestrator.py
Épico 3 — Orquestrador do Pipeline Completo.

Encadeia as etapas em sequência garantindo que cada passo
só inicie se o anterior foi concluído com sucesso:

  1. Extração       → ingestion/extractor.py
  2. Processamento  → processing/llm_client.py
  3. Validação      → pipeline/quarantine.py
  4. Geração Mídia  → publishing/image_generator.py
  5. Publicação     → publishing/instagram_client.py

Abort gracioso: se a API da Câmara estiver offline (0 registros capturados
e base vazia), o pipeline aborta antes de chamar etapas pagas (LLM, Placid).
"""

import os
import traceback
from datetime import datetime

from dotenv import load_dotenv

from ingestion.extractor import LegislativeExtractor
from processing.llm_client import LLMProcessor
from pipeline.quarantine import QuarantineValidator, QuarantineReporter
from publishing.image_generator import ImageGenerator
from publishing.instagram_client import InstagramClient
from observability.alerting import Alerter
from models.database import check_connection
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

# Lote máximo de publicações por run (estratégia de conteúdo)
_PUBLISH_BATCH_SIZE = int(os.getenv("PUBLISH_BATCH_SIZE", "3"))
# Lote máximo de projetos para processar na LLM por run
_LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "10"))


class Pipeline:
    """
    Executa o pipeline completo de automação do PautaLimpa.

    Cada etapa é atômica: falha em uma etapa não afeta registros
    já processados em etapas anteriores (status persistido no banco).
    """

    def __init__(self) -> None:
        self._extractor       = LegislativeExtractor()
        self._llm_processor   = LLMProcessor()
        self._quarantine      = QuarantineValidator()
        self._reporter        = QuarantineReporter()
        self._image_generator = ImageGenerator()
        self._instagram       = InstagramClient()
        self._alerter         = Alerter()

    def run(self) -> dict:
        """
        Executa todas as etapas do pipeline em sequência.

        Returns:
            dict: Resumo de cada etapa com estatísticas.
        """
        inicio = datetime.now()
        logger.info("=" * 70)
        logger.info("PIPELINE INICIADO — %s", inicio.strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("=" * 70)

        resultado = {
            "inicio":        inicio.isoformat(),
            "fim":           None,
            "status":        "SUCESSO",
            "etapas":        {},
        }

        # ------------------------------------------------------------------
        # Pré-condição: conectividade com o banco de dados
        # ------------------------------------------------------------------
        if not check_connection():
            msg = "Banco de dados inacessível — pipeline abortado."
            logger.critical(msg)
            self._alerter.send_critical(msg)
            resultado["status"] = "ABORTADO"
            return resultado

        # ------------------------------------------------------------------
        # Etapa 1: Extração da API da Câmara
        # ------------------------------------------------------------------
        logger.info("--- ETAPA 1: Extração ---")
        try:
            stats_extracao = self._extractor.run()
            resultado["etapas"]["extracao"] = stats_extracao

            # Abort gracioso: API offline E banco sem itens pendentes
            if stats_extracao["inseridos"] == 0 and stats_extracao["erros"] > 0:
                total_pendentes = self._count_pending()
                if total_pendentes == 0:
                    msg = "API da Câmara parece offline e não há itens pendentes. Pipeline abortado para evitar execuções vazias."
                    logger.warning(msg)
                    self._alerter.send_warning(msg)
                    resultado["status"] = "ABORTADO_SEM_DADOS"
                    return resultado

        except Exception as exc:
            self._handle_critical_error("Extração", exc, resultado)
            return resultado

        # ------------------------------------------------------------------
        # Etapa 2: Processamento LLM
        # ------------------------------------------------------------------
        logger.info("--- ETAPA 2: Processamento LLM ---")
        try:
            stats_llm = self._llm_processor.run(batch_size=_LLM_BATCH_SIZE)
            resultado["etapas"]["llm"] = stats_llm
        except Exception as exc:
            self._handle_critical_error("Processamento LLM", exc, resultado)
            return resultado

        # ------------------------------------------------------------------
        # Etapa 3: Validação (Quarentena)
        # ------------------------------------------------------------------
        logger.info("--- ETAPA 3: Validação / Quarentena ---")
        try:
            stats_quarentena = self._quarantine.validate_all()
            resultado["etapas"]["quarentena"] = stats_quarentena
        except Exception as exc:
            logger.error("Erro na etapa de quarentena (não bloqueante): %s", exc)
            resultado["etapas"]["quarentena"] = {"erro": str(exc)}

        # ------------------------------------------------------------------
        # Etapa 4: Geração de Mídia (Imagem)
        # ------------------------------------------------------------------
        logger.info("--- ETAPA 4: Geração de Mídia ---")
        try:
            stats_midia = self._image_generator.process_queue()
            resultado["etapas"]["midia"] = stats_midia
        except Exception as exc:
            self._handle_critical_error("Geração de Mídia", exc, resultado)
            return resultado

        # ------------------------------------------------------------------
        # Etapa 5: Publicação no Instagram (máx. PUBLISH_BATCH_SIZE leis)
        # ------------------------------------------------------------------
        logger.info("--- ETAPA 5: Publicação Instagram ---")
        try:
            stats_publicacao = self._instagram.process_queue(batch_size=_PUBLISH_BATCH_SIZE)
            resultado["etapas"]["publicacao"] = stats_publicacao

            if stats_publicacao["publicados"] == 0 and stats_publicacao["processados"] == 0:
                logger.info("Nada a publicar neste ciclo.")

        except Exception as exc:
            logger.error("Erro na etapa de publicação: %s", exc)
            resultado["etapas"]["publicacao"] = {"erro": str(exc)}
            resultado["status"] = "PARCIAL"

        # ------------------------------------------------------------------
        # Etapa 6: Relatório de Quarentena (assíncrono, não bloqueante)
        # ------------------------------------------------------------------
        logger.info("--- ETAPA 6: Relatório de Quarentena (e-mail) ---")
        try:
            self._reporter.send_daily_report()
        except Exception as exc:
            logger.warning("Erro ao enviar relatório de quarentena: %s", exc)

        # ------------------------------------------------------------------
        # Etapa 7: Coleta de Métricas de Engajamento (posts de 24h atrás)
        # ------------------------------------------------------------------
        logger.info("--- ETAPA 7: Coleta de Métricas ---")
        try:
            stats_metricas = self._instagram.collect_insights()
            resultado["etapas"]["metricas"] = stats_metricas
        except Exception as exc:
            logger.warning("Erro ao coletar métricas (não bloqueante): %s", exc)

        # ------------------------------------------------------------------
        # Finalização
        # ------------------------------------------------------------------
        fim = datetime.now()
        resultado["fim"] = fim.isoformat()
        duracao = (fim - inicio).total_seconds()

        logger.info("=" * 70)
        logger.info(
            "PIPELINE CONCLUÍDO em %.1fs | status=%s",
            duracao, resultado["status"],
        )
        logger.info("=" * 70)
        return resultado

    def _count_pending(self) -> int:
        """Conta projetos com status AGUARDANDO_IA no banco."""
        from sqlalchemy import text
        from models.database import get_session
        with get_session() as session:
            result = session.execute(
                text("SELECT COUNT(*) FROM projetos_brutos WHERE status_processamento = 'AGUARDANDO_IA'")
            )
            return result.scalar() or 0

    def _handle_critical_error(self, etapa: str, exc: Exception, resultado: dict) -> None:
        """Registra erro crítico, alerta equipe e marca pipeline como FALHA."""
        msg = f"Erro crítico na etapa '{etapa}': {exc}"
        logger.critical(msg, exc_info=True)
        resultado["status"] = "FALHA"
        resultado["etapas"][etapa.lower().replace(" ", "_")] = {
            "erro": str(exc),
            "traceback": traceback.format_exc(),
        }
        self._alerter.send_critical(msg)


if __name__ == "__main__":
    pipeline = Pipeline()
    resultado = pipeline.run()
    raise SystemExit(0 if resultado["status"] in ("SUCESSO", "PARCIAL") else 1)

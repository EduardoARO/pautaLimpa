"""
pipeline/scheduler.py
Épico 3 — Gatilho Temporal (Cron Job) via APScheduler.

Agenda:
  - Extração + Processamento + Publicação: diariamente às 18h00 (horário configurável)
  - Coleta de Métricas de Engajamento: diariamente às 10h00 (posts de ontem)
  - Limpeza de arquivos e logs antigos: diariamente às 02h00 (madrugada)
  - Backup do banco de dados: diariamente às 03h00 (madrugada)

Execução:
  python -m pipeline.scheduler
"""

import os

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

# Horário de publicação principal (pico orgânico do Instagram)
_PUBLISH_HOUR   = int(os.getenv("SCHEDULE_PUBLISH_HOUR", "18"))
_PUBLISH_MINUTE = int(os.getenv("SCHEDULE_PUBLISH_MINUTE", "0"))

# Horário de coleta de métricas (posts de ontem com 24h+)
_METRICS_HOUR   = int(os.getenv("SCHEDULE_METRICS_HOUR", "10"))
_METRICS_MINUTE = int(os.getenv("SCHEDULE_METRICS_MINUTE", "0"))

# Horário de limpeza (madrugada)
_CLEANUP_HOUR   = int(os.getenv("SCHEDULE_CLEANUP_HOUR", "2"))
_BACKUP_HOUR    = int(os.getenv("SCHEDULE_BACKUP_HOUR", "3"))

# Fuso horário da publicação
_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "America/Sao_Paulo")


def job_pipeline_completo() -> None:
    """Job principal: executa o pipeline completo (extração → publicação)."""
    logger.info("SCHEDULER: iniciando job_pipeline_completo...")
    try:
        from pipeline.orchestrator import Pipeline
        resultado = Pipeline().run()
        logger.info("SCHEDULER: pipeline concluído | status=%s", resultado.get("status"))
    except Exception as exc:
        logger.error("SCHEDULER: erro no job_pipeline_completo: %s", exc, exc_info=True)


def job_coletar_metricas() -> None:
    """Job de métricas: coleta engajamento de posts publicados há 24h+."""
    logger.info("SCHEDULER: iniciando job_coletar_metricas...")
    try:
        from publishing.instagram_client import InstagramClient
        stats = InstagramClient().collect_insights()
        logger.info("SCHEDULER: métricas coletadas | %s", stats)
    except Exception as exc:
        logger.error("SCHEDULER: erro no job_coletar_metricas: %s", exc, exc_info=True)


def job_limpeza() -> None:
    """Job de limpeza: remove imagens expiradas e logs antigos."""
    logger.info("SCHEDULER: iniciando job_limpeza...")
    try:
        from observability.cleanup import CleanupService
        CleanupService().run()
    except Exception as exc:
        logger.error("SCHEDULER: erro no job_limpeza: %s", exc, exc_info=True)


def job_backup() -> None:
    """Job de backup: executa pg_dump e envia para armazenamento externo."""
    logger.info("SCHEDULER: iniciando job_backup...")
    try:
        from governance.backup import BackupService
        BackupService().run()
    except Exception as exc:
        logger.error("SCHEDULER: erro no job_backup: %s", exc, exc_info=True)


def start_scheduler() -> None:
    """Inicializa e bloqueia o processo com todos os jobs agendados."""
    scheduler = BlockingScheduler(timezone=_TIMEZONE)

    # Pipeline principal (extração + IA + publicação) — pico orgânico do Instagram
    scheduler.add_job(
        job_pipeline_completo,
        trigger=CronTrigger(hour=_PUBLISH_HOUR, minute=_PUBLISH_MINUTE, timezone=_TIMEZONE),
        id="pipeline_completo",
        name="Pipeline completo (extração → publicação)",
        misfire_grace_time=300,   # Tolera 5min de atraso antes de desistir
        coalesce=True,            # Se disparou várias vezes perdidas, executa uma só vez
    )

    # Coleta de métricas (posts de ontem)
    scheduler.add_job(
        job_coletar_metricas,
        trigger=CronTrigger(hour=_METRICS_HOUR, minute=_METRICS_MINUTE, timezone=_TIMEZONE),
        id="coletar_metricas",
        name="Coleta de métricas de engajamento",
        misfire_grace_time=600,
        coalesce=True,
    )

    # Limpeza de arquivos temporários e logs
    scheduler.add_job(
        job_limpeza,
        trigger=CronTrigger(hour=_CLEANUP_HOUR, minute=0, timezone=_TIMEZONE),
        id="limpeza",
        name="Limpeza de arquivos temporários e logs antigos",
        misfire_grace_time=3600,
        coalesce=True,
    )

    # Backup do banco de dados
    scheduler.add_job(
        job_backup,
        trigger=CronTrigger(hour=_BACKUP_HOUR, minute=0, timezone=_TIMEZONE),
        id="backup",
        name="Backup do banco de dados",
        misfire_grace_time=3600,
        coalesce=True,
    )

    logger.info("=" * 60)
    logger.info("PautaLimpa Scheduler iniciado | fuso=%s", _TIMEZONE)
    logger.info("Pipeline principal: %02dh%02d diariamente", _PUBLISH_HOUR, _PUBLISH_MINUTE)
    logger.info("Métricas:           %02dh%02d diariamente", _METRICS_HOUR, _METRICS_MINUTE)
    logger.info("Limpeza:            %02dh00 diariamente", _CLEANUP_HOUR)
    logger.info("Backup:             %02dh00 diariamente", _BACKUP_HOUR)
    logger.info("Pressione Ctrl+C para encerrar.")
    logger.info("=" * 60)

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler encerrado pelo operador.")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    start_scheduler()

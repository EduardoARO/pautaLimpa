"""
observability/cleanup.py
Épico 5 — Retenção e Limpeza de Arquivos Temporários.

Rotinas:
  - Deletar URLs de imagens temporárias após 24h da publicação
    (o arquivo em si é deletado pelo provedor Placid automaticamente;
     aqui apenas nullificamos a referência no banco para higiene)
  - Arquivar logs de auditoria com mais de 90 dias (comprimir + mover)
  - Limpar arquivos .log rotativos com mais de 30 dias no disco
"""

import gzip
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from models.database import get_session
from utils.logger import get_logger

load_dotenv_needed = False
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = get_logger(__name__)

_LOG_DIR              = Path(os.getenv("LOG_DIR", "./logs"))
_LOG_RETENTION_DAYS   = int(os.getenv("LOG_RETENTION_DAYS", "30"))
_AUDIT_ARCHIVE_DAYS   = int(os.getenv("AUDIT_ARCHIVE_DAYS", "90"))


class CleanupService:
    """Executa todas as rotinas de limpeza em sequência."""

    def run(self) -> dict[str, int]:
        """
        Executa todas as rotinas de limpeza.

        Returns:
            dict: Contadores por tipo de limpeza realizada.
        """
        stats = {
            "imagens_nullificadas": 0,
            "logs_removidos":       0,
            "audit_arquivados":     0,
        }

        stats["imagens_nullificadas"] = self._cleanup_expired_images()
        stats["logs_removidos"]       = self._cleanup_old_log_files()
        stats["audit_arquivados"]     = self._archive_old_audit_records()

        logger.info(
            "Limpeza concluída | imagens=%d | logs=%d | auditoria=%d",
            stats["imagens_nullificadas"],
            stats["logs_removidos"],
            stats["audit_arquivados"],
        )
        return stats

    def _cleanup_expired_images(self) -> int:
        """
        Nullifica url_imagem em publicacoes_instagram cujo
        data_delecao_imagem já passou (gerada automaticamente: publicacao + 24h).
        """
        sql = text("""
            UPDATE publicacoes_instagram
            SET url_imagem = NULL
            WHERE status = 'PUBLICADO'
              AND data_delecao_imagem IS NOT NULL
              AND data_delecao_imagem <= NOW()
              AND url_imagem IS NOT NULL
        """)
        with get_session() as session:
            try:
                result = session.execute(sql)
                session.commit()
                count = result.rowcount
                if count:
                    logger.info("Imagens expiradas nullificadas: %d registros.", count)
                return count
            except Exception as exc:
                session.rollback()
                logger.error("Erro ao nullificar imagens expiradas: %s", exc)
                return 0

    def _cleanup_old_log_files(self) -> int:
        """
        Remove arquivos .log no diretório de logs com mais de LOG_RETENTION_DAYS dias.
        Preserva o arquivo principal pauta_limpa.log (rotativo pelo handler).
        """
        if not _LOG_DIR.exists():
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=_LOG_RETENTION_DAYS)
        removed = 0

        for log_file in _LOG_DIR.glob("*.log.*"):
            try:
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    log_file.unlink()
                    removed += 1
                    logger.debug("Log removido: %s", log_file.name)
            except OSError as exc:
                logger.warning("Erro ao remover log %s: %s", log_file.name, exc)

        if removed:
            logger.info("Logs antigos removidos: %d arquivo(s).", removed)
        return removed

    def _archive_old_audit_records(self) -> int:
        """
        Marca registros de auditoria com mais de AUDIT_ARCHIVE_DAYS dias como arquivados
        adicionando flag num campo observacao. Em produção, pode mover para tabela cold storage.
        """
        sql = text("""
            UPDATE auditoria
            SET observacao = CONCAT('[ARQUIVADO] ', COALESCE(observacao, ''))
            WHERE registrado_em < NOW() - INTERVAL ':days days'
              AND (observacao IS NULL OR observacao NOT LIKE '[ARQUIVADO]%')
        """)
        with get_session() as session:
            try:
                result = session.execute(
                    text(f"""
                        UPDATE auditoria
                        SET observacao = CONCAT('[ARQUIVADO] ', COALESCE(observacao, ''))
                        WHERE registrado_em < NOW() - INTERVAL '{_AUDIT_ARCHIVE_DAYS} days'
                          AND (observacao IS NULL OR observacao NOT LIKE '[ARQUIVADO]%')
                    """)
                )
                session.commit()
                count = result.rowcount
                if count:
                    logger.info("Registros de auditoria arquivados: %d.", count)
                return count
            except Exception as exc:
                session.rollback()
                logger.error("Erro ao arquivar registros de auditoria: %s", exc)
                return 0

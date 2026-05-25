"""
governance/backup.py
Épico 6 — Rotina Automatizada de Backup do Banco de Dados.

Fluxo:
  1. Executa pg_dump do banco PostgreSQL
  2. Comprime o arquivo com gzip
  3. Envia para AWS S3 (ou Google Cloud Storage se configurado)
  4. Remove backups locais temporários após upload
  5. Aplica política de retenção:
     - Últimos 7 backups diários
     - 1 backup por semana (últimas 4 semanas)
"""

import gzip
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

_DB_HOST     = os.getenv("DB_HOST", "localhost")
_DB_PORT     = os.getenv("DB_PORT", "5432")
_DB_NAME     = os.getenv("DB_NAME", "pauta_limpa")
_DB_USER     = os.getenv("DB_USER", "postgres")
_DB_PASSWORD = os.getenv("DB_PASSWORD", "")

_S3_BUCKET   = os.getenv("BACKUP_S3_BUCKET", "")
_S3_PREFIX   = os.getenv("BACKUP_S3_PREFIX", "pautalimpa/backups/")
_BACKUP_DIR  = Path(os.getenv("BACKUP_LOCAL_DIR", "./backups"))

# Política de retenção
_DAILY_RETENTION  = int(os.getenv("BACKUP_DAILY_RETENTION",  "7"))
_WEEKLY_RETENTION = int(os.getenv("BACKUP_WEEKLY_RETENTION", "4"))


class BackupService:
    """Executa backup do banco, comprime, envia ao S3 e gerencia retenção."""

    def run(self) -> dict[str, str]:
        """
        Executa o ciclo completo de backup.

        Returns:
            dict: {"arquivo_local", "arquivo_s3", "status"}
        """
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp    = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename     = f"pauta_limpa_backup_{timestamp}.sql.gz"
        local_path   = _BACKUP_DIR / filename

        try:
            # Etapa 1: pg_dump + compressão gzip em memória
            logger.info("Iniciando pg_dump do banco '%s'...", _DB_NAME)
            self._dump_and_compress(local_path)
            size_mb = local_path.stat().st_size / (1024 * 1024)
            logger.info("Dump concluído: %s (%.2f MB)", filename, size_mb)

            # Etapa 2: Upload para S3
            s3_key = ""
            if _S3_BUCKET:
                s3_key = self._upload_to_s3(local_path, filename)
                logger.info("Backup enviado ao S3: s3://%s/%s", _S3_BUCKET, s3_key)
            else:
                logger.warning(
                    "BACKUP_S3_BUCKET não configurado — backup mantido apenas local em %s", local_path
                )

            # Etapa 3: Política de retenção (remove backups antigos locais)
            self._apply_retention_policy()

            return {"arquivo_local": str(local_path), "arquivo_s3": s3_key, "status": "SUCESSO"}

        except Exception as exc:
            logger.error("Backup FALHOU: %s", exc, exc_info=True)
            return {"arquivo_local": str(local_path), "arquivo_s3": "", "status": f"FALHA: {exc}"}

    def _dump_and_compress(self, output_path: Path) -> None:
        """Executa pg_dump e comprime a saída com gzip."""
        env = os.environ.copy()
        env["PGPASSWORD"] = _DB_PASSWORD

        cmd = [
            "pg_dump",
            "--host",     _DB_HOST,
            "--port",     _DB_PORT,
            "--username", _DB_USER,
            "--no-password",
            "--format",   "plain",
            "--encoding", "UTF8",
            _DB_NAME,
        ]

        with gzip.open(output_path, "wb") as gz_file:
            result = subprocess.run(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            gz_file.write(result.stdout)

        if result.returncode != 0:
            raise RuntimeError(
                f"pg_dump falhou com código {result.returncode}: {result.stderr.decode()}"
            )

    def _upload_to_s3(self, local_path: Path, filename: str) -> str:
        """Faz upload do arquivo para o bucket S3 configurado."""
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "Pacote 'boto3' não instalado. Execute: pip install boto3"
            )

        s3_client = boto3.client("s3")
        s3_key    = f"{_S3_PREFIX}{filename}"

        s3_client.upload_file(
            str(local_path),
            _S3_BUCKET,
            s3_key,
            ExtraArgs={"ServerSideEncryption": "AES256"},
        )
        return s3_key

    def _apply_retention_policy(self) -> None:
        """
        Remove backups locais antigos conforme a política de retenção:
          - Mantém os últimos BACKUP_DAILY_RETENTION backups diários
          - Mantém 1 por semana das últimas BACKUP_WEEKLY_RETENTION semanas
        """
        backups = sorted(
            _BACKUP_DIR.glob("pauta_limpa_backup_*.sql.gz"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        # Mantém os N mais recentes (diários)
        to_keep = set(backups[:_DAILY_RETENTION])

        # Adiciona 1 por semana das últimas N semanas
        now = datetime.now(timezone.utc)
        for week_offset in range(1, _WEEKLY_RETENTION + 1):
            week_start = now - timedelta(weeks=week_offset)
            week_end   = now - timedelta(weeks=week_offset - 1)
            weekly_candidate = next(
                (
                    b for b in backups
                    if week_start
                    <= datetime.fromtimestamp(b.stat().st_mtime, tz=timezone.utc)
                    <= week_end
                ),
                None,
            )
            if weekly_candidate:
                to_keep.add(weekly_candidate)

        # Remove os que não estão na lista de retenção
        for backup in backups:
            if backup not in to_keep:
                backup.unlink()
                logger.debug("Backup local removido (retenção): %s", backup.name)

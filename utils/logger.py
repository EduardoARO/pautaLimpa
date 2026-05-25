"""
utils/logger.py
Configuração centralizada de logging para toda a aplicação PautaLimpa.
Registra simultaneamente no console (stdout) e em arquivo rotativo no disco.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger(name: str) -> logging.Logger:
    """
    Retorna um logger configurado com handlers de console e arquivo.

    Args:
        name: Nome do módulo chamador (usar __name__ é a convenção).

    Returns:
        logging.Logger: Instância configurada e pronta para uso.
    """
    # Lê nível e diretório de log a partir do ambiente (com fallback seguro)
    log_level_str: str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level: int = getattr(logging, log_level_str, logging.INFO)
    log_dir: Path = Path(os.getenv("LOG_DIR", "./logs"))

    # Garante que o diretório de logs exista antes de tentar escrever
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)

    # Evita adicionar handlers duplicados em chamadas repetidas (ex: em testes)
    if logger.handlers:
        return logger

    logger.setLevel(log_level)

    # Formato padronizado: timestamp | nível | módulo | mensagem
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler 1: saída no console (útil em desenvolvimento e CI/CD)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)

    # Handler 2: arquivo rotativo — máximo 5 MB por arquivo, mantém 3 backups
    log_file = log_dir / "pauta_limpa.log"
    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger

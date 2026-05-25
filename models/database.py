"""
models/database.py
Configuração da engine SQLAlchemy e fábrica de sessões.
Centraliza a conexão com o PostgreSQL usando variáveis de ambiente.
"""

import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import OperationalError
from dotenv import load_dotenv

from utils.logger import get_logger

load_dotenv(override=True)

logger = get_logger(__name__)


def build_database_url() -> str:
    """
    Constrói a URL de conexão PostgreSQL a partir das variáveis de ambiente.
    Prioriza DATABASE_URL se definida; caso contrário, monta a partir dos
    campos individuais (DB_HOST, DB_PORT, etc.).

    Returns:
        str: URL de conexão no formato SQLAlchemy.

    Raises:
        ValueError: Se nenhuma configuração válida for encontrada.
    """
    # Prioridade 1: URL completa já configurada
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return database_url

    # Prioridade 2: campos individuais
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")

    if not all([name, user, password]):
        raise ValueError(
            "Configuração de banco incompleta. Defina DATABASE_URL ou "
            "DB_HOST, DB_PORT, DB_NAME, DB_USER e DB_PASSWORD no .env"
        )

    return (
        f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{name}"
    )


def get_database_diagnostics() -> dict:
    database_url = os.getenv("DATABASE_URL")
    return {
        "database_url_configured": bool(database_url),
        "db_host": os.getenv("DB_HOST", "localhost"),
        "db_port": os.getenv("DB_PORT", "5432"),
        "db_name": os.getenv("DB_NAME"),
        "db_user": os.getenv("DB_USER"),
        "db_password_configured": bool(os.getenv("DB_PASSWORD")),
    }


# Cria a engine uma única vez (padrão Singleton implícito via módulo Python)
_engine = create_engine(
    build_database_url(),
    pool_pre_ping=True,     # Valida conexões do pool antes de usá-las
    pool_size=5,            # Conexões mantidas abertas no pool
    max_overflow=10,        # Conexões extras permitidas acima do pool_size
    echo=False,             # True = imprime todas as queries (debug)
)

# Fábrica de sessões: autocommit e autoflush desativados para controle explícito
SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)


def get_session() -> Session:
    """
    Retorna uma nova sessão do banco de dados.
    Use como context manager para garantir fechamento automático:

        with get_session() as session:
            session.query(...)

    Returns:
        Session: Sessão SQLAlchemy pronta para uso.
    """
    return SessionLocal()


def check_connection() -> bool:
    """
    Testa a conectividade com o banco de dados executando uma query trivial.

    Returns:
        bool: True se a conexão estiver saudável, False caso contrário.
    """
    try:
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Conexão com o banco de dados verificada com sucesso.")
        return True
    except OperationalError as exc:
        logger.error("Falha ao conectar com o banco de dados: %s", exc)
        return False

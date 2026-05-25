import os
import time

import requests
from dotenv import load_dotenv
from sqlalchemy import text

from models.database import get_session
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

_BASE_URL = os.getenv("API_BASE_URL", "https://dadosabertos.camara.leg.br/api/v2")
_LIMIT = int(os.getenv("BACKFILL_DATES_LIMIT", "1000"))
_DELAY = float(os.getenv("BACKFILL_DATES_DELAY_SECONDS", "0.2"))

_FETCH_MISSING_SQL = text("""
    SELECT id, id_origem
    FROM projetos_brutos
    WHERE data_apresentacao IS NULL
    ORDER BY id ASC
    LIMIT :limit
""")

_UPDATE_SQL = text("""
    UPDATE projetos_brutos
    SET data_apresentacao = :data_apresentacao,
        data_atualizacao = NOW()
    WHERE id = :id
""")


def fetch_date(id_origem: str) -> str | None:
    response = requests.get(
        f"{_BASE_URL}/proposicoes/{id_origem}",
        headers={"Accept": "application/json", "User-Agent": "PautaLimpa-Bot/1.0"},
        timeout=20,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json().get("dados", {}).get("dataApresentacao")


def main() -> None:
    with get_session() as session:
        rows = session.execute(_FETCH_MISSING_SQL, {"limit": _LIMIT}).fetchall()

    if not rows:
        logger.info("BACKFILL: nenhum projeto sem data_apresentacao.")
        return

    updated = 0
    missing = 0
    for row in rows:
        try:
            data_apresentacao = fetch_date(row.id_origem)
            if data_apresentacao:
                with get_session() as session:
                    session.execute(_UPDATE_SQL, {"id": row.id, "data_apresentacao": data_apresentacao})
                    session.commit()
                updated += 1
                logger.info("BACKFILL: id=%s origem=%s data=%s", row.id, row.id_origem, data_apresentacao)
            else:
                missing += 1
                logger.warning("BACKFILL: sem data para id=%s origem=%s", row.id, row.id_origem)
        except Exception as exc:
            missing += 1
            logger.error("BACKFILL: erro id=%s origem=%s: %s", row.id, row.id_origem, exc)
        time.sleep(_DELAY)

    logger.info("BACKFILL concluído | atualizados=%d | sem_data=%d", updated, missing)


if __name__ == "__main__":
    main()

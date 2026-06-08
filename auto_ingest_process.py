import os
import time
from datetime import datetime

from dotenv import load_dotenv

from pipeline.hourly_cycle import run_ingest_and_process_once
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

_INGEST_INTERVAL_MINUTES = int(os.getenv("INGEST_INTERVAL_MINUTES", "60"))
_INTERVAL_SECONDS = int(os.getenv("AUTO_PIPELINE_INTERVAL_SECONDS", str(_INGEST_INTERVAL_MINUTES * 60)))
_RUN_ON_START = os.getenv("AUTO_PIPELINE_RUN_ON_START", "true").lower() == "true"
_PROCESS_UNTIL_EMPTY = os.getenv("AUTO_PROCESS_UNTIL_EMPTY", "false").lower() == "true"
_RUN_TIMES = [
    item.strip()
    for item in os.getenv("AUTO_PIPELINE_RUN_TIMES", "00:00,12:00").split(",")
    if item.strip()
]


def run_once() -> None:
    logger.info("AUTO: iniciando ciclo horário compartilhado...")
    stats = run_ingest_and_process_once(process_until_empty=_PROCESS_UNTIL_EMPTY)
    logger.info("AUTO: ciclo horário finalizado | %s", stats)


def main() -> None:
    logger.info("AUTO: watcher iniciado | horários=%s | process_until_empty=%s", _RUN_TIMES, _PROCESS_UNTIL_EMPTY)
    logger.info("AUTO: intervalo base=%d minuto(s) | override_segundos=%d", _INGEST_INTERVAL_MINUTES, _INTERVAL_SECONDS)
    first_run = True
    last_run_key = None
    while True:
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_key = now.strftime("%Y-%m-%d %H:%M")
        should_run = current_time in _RUN_TIMES and current_key != last_run_key

        if first_run and _RUN_ON_START:
            should_run = True
            current_key = f"startup-{now.isoformat(timespec='seconds')}"

        if should_run:
            try:
                run_once()
                last_run_key = current_key
            except Exception as exc:
                logger.error("AUTO: erro no ciclo automático: %s", exc, exc_info=True)
            first_run = False
        elif first_run:
            first_run = False

        logger.info("AUTO: aguardando %ds até nova checagem de horário...", _INTERVAL_SECONDS)
        time.sleep(_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

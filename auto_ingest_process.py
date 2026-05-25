import os
import time
from datetime import datetime

from dotenv import load_dotenv

from ingestion.extractor import LegislativeExtractor
from processing.llm_client import LLMProcessor
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

_INTERVAL_SECONDS = int(os.getenv("AUTO_PIPELINE_INTERVAL_SECONDS", "900"))
_LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "1"))
_RUN_ON_START = os.getenv("AUTO_PIPELINE_RUN_ON_START", "true").lower() == "true"
_PROCESS_UNTIL_EMPTY = os.getenv("AUTO_PROCESS_UNTIL_EMPTY", "false").lower() == "true"
_RUN_TIMES = [
    item.strip()
    for item in os.getenv("AUTO_PIPELINE_RUN_TIMES", "00:00,12:00").split(",")
    if item.strip()
]


def run_once() -> None:
    logger.info("AUTO: iniciando extração segura (sem publicação)...")
    extraction_stats = LegislativeExtractor().run()
    logger.info("AUTO: extração finalizada | %s", extraction_stats)

    logger.info("AUTO: iniciando processamento IA (batch_size=%d)...", _LLM_BATCH_SIZE)
    while True:
        llm_stats = LLMProcessor().run(batch_size=_LLM_BATCH_SIZE)
        logger.info("AUTO: processamento IA finalizado | %s", llm_stats)
        if not _PROCESS_UNTIL_EMPTY or llm_stats.get("processados", 0) == 0:
            break


def main() -> None:
    logger.info("AUTO: watcher iniciado | horários=%s | batch_ia=%d", _RUN_TIMES, _LLM_BATCH_SIZE)
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

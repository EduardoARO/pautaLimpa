import os
import time

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
    logger.info("AUTO: watcher iniciado | intervalo=%ds | batch_ia=%d", _INTERVAL_SECONDS, _LLM_BATCH_SIZE)
    first_run = True
    while True:
        if first_run and not _RUN_ON_START:
            first_run = False
        else:
            try:
                run_once()
            except Exception as exc:
                logger.error("AUTO: erro no ciclo automático: %s", exc, exc_info=True)
            first_run = False

        logger.info("AUTO: aguardando %ds até o próximo ciclo...", _INTERVAL_SECONDS)
        time.sleep(_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

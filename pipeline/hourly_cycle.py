"""pipeline/hourly_cycle.py

Rotina compartilhada para o ciclo horário de ingestão + processamento IA.
Usada pelo scheduler APScheduler e pelo watcher local para evitar duplicação.
"""

from __future__ import annotations

import os
import time

from dotenv import load_dotenv

from ingestion.extractor import LegislativeExtractor
from processing.llm_client import LLMProcessor
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

_DEFAULT_LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "1"))
_DEFAULT_PROCESS_UNTIL_EMPTY = os.getenv("AUTO_PROCESS_UNTIL_EMPTY", "true").lower() == "true"
_DEFAULT_DRAIN_SLEEP_SECONDS = float(os.getenv("AUTO_DRAIN_SLEEP_SECONDS", "0"))


def run_ingest_and_process_once(
    batch_size: int | None = None,
    process_until_empty: bool | None = None,
) -> dict:
    """Executa uma rodada de ingestão e processamento IA.

    Returns:
        dict com stats de extração e dos ciclos de LLM.
    """
    resolved_batch_size = batch_size or _DEFAULT_LLM_BATCH_SIZE
    resolved_process_until_empty = (
        _DEFAULT_PROCESS_UNTIL_EMPTY if process_until_empty is None else process_until_empty
    )

    logger.info("CICLO_HORARIO: iniciando extração segura (sem publicação)...")
    extraction_stats = LegislativeExtractor().run()
    logger.info("CICLO_HORARIO: extração finalizada | %s", extraction_stats)

    logger.info(
        "CICLO_HORARIO: iniciando processamento IA | batch_size=%d | until_empty=%s",
        resolved_batch_size,
        resolved_process_until_empty,
    )

    processor = LLMProcessor()
    llm_runs: list[dict] = []
    while True:
        llm_stats = processor.run(batch_size=resolved_batch_size)
        llm_runs.append(llm_stats)
        logger.info("CICLO_HORARIO: processamento IA finalizado | %s", llm_stats)
        if not resolved_process_until_empty or llm_stats.get("processados", 0) == 0:
            break

    return {
        "extracao": extraction_stats,
        "llm_runs": llm_runs,
        "llm_total_processados": sum(run.get("processados", 0) for run in llm_runs),
        "llm_total_sucesso": sum(run.get("sucesso", 0) for run in llm_runs),
        "llm_total_quarentena": sum(run.get("quarentena", 0) for run in llm_runs),
        "llm_total_erro": sum(run.get("erro", 0) for run in llm_runs),
        "llm_total_adiados": sum(run.get("adiados", 0) for run in llm_runs),
    }


def run_until_drained(
    batch_size: int | None = None,
    process_until_empty: bool | None = None,
    sleep_seconds: float | None = None,
) -> dict:
    """Repete ciclos de ingestão + processamento até não haver mais registros novos a adicionar.

    O ciclo para quando a extração não inserir registros novos e a fila da LLM não
    processar nenhum item no mesmo round.
    """
    resolved_sleep_seconds = _DEFAULT_DRAIN_SLEEP_SECONDS if sleep_seconds is None else sleep_seconds
    cycles: list[dict] = []

    while True:
        cycle_stats = run_ingest_and_process_once(
            batch_size=batch_size,
            process_until_empty=process_until_empty,
        )
        cycles.append(cycle_stats)

        inserted = cycle_stats.get("extracao", {}).get("inseridos", 0)
        processed = cycle_stats.get("llm_total_processados", 0)

        logger.info(
            "CICLO_DRAIN: ciclo concluído | inseridos=%d | processados=%d | sleep=%.1fs",
            inserted,
            processed,
            resolved_sleep_seconds,
        )

        if inserted == 0 and processed == 0:
            break

        if resolved_sleep_seconds > 0:
            time.sleep(resolved_sleep_seconds)

    return {
        "cycles": cycles,
        "total_cycles": len(cycles),
        "total_inseridos": sum(c.get("extracao", {}).get("inseridos", 0) for c in cycles),
        "total_processados": sum(c.get("llm_total_processados", 0) for c in cycles),
        "total_sucesso": sum(c.get("llm_total_sucesso", 0) for c in cycles),
        "total_quarentena": sum(c.get("llm_total_quarentena", 0) for c in cycles),
        "total_erro": sum(c.get("llm_total_erro", 0) for c in cycles),
        "total_adiados": sum(c.get("llm_total_adiados", 0) for c in cycles),
    }

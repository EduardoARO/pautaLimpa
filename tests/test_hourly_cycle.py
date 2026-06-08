from __future__ import annotations

from types import SimpleNamespace

from pipeline.hourly_cycle import run_ingest_and_process_once


class FakeExtractor:
    def run(self):
        return {"inseridos": 2, "erros": 0}


class FakeProcessor:
    def __init__(self):
        self.calls = []

    def run(self, batch_size=1):
        self.calls.append(batch_size)
        if len(self.calls) == 1:
            return {"processados": 1, "sucesso": 1, "quarentena": 0, "erro": 0, "adiados": 0}
        return {"processados": 0, "sucesso": 0, "quarentena": 0, "erro": 0, "adiados": 0}


def test_hourly_cycle_runs_extraction_and_llm_until_empty(monkeypatch):
    processor = FakeProcessor()
    monkeypatch.setattr("pipeline.hourly_cycle.LegislativeExtractor", FakeExtractor)
    monkeypatch.setattr("pipeline.hourly_cycle.LLMProcessor", lambda: processor)

    stats = run_ingest_and_process_once(batch_size=2, process_until_empty=True)

    assert stats["extracao"]["inseridos"] == 2
    assert stats["llm_total_processados"] == 1
    assert processor.calls == [2, 2]

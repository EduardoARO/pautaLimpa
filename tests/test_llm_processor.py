from __future__ import annotations

from types import SimpleNamespace

import pytest

from processing.llm_client import LLMProcessor


class FakePromptManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def build_messages(self, projeto: dict, tipo_analise: str = "IMPARCIAL"):
        self.calls.append(tipo_analise)
        return ([{"role": "system", "content": f"system-{tipo_analise}"}, {"role": "user", "content": "user"}], 99)


class FakeProvider:
    def __init__(self, text: str = "PL - 1234/2024\nTexto suficiente para passar na validação." ) -> None:
        self.text = text
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        return {
            "text": self.text,
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "model": "fake-model",
        }


@pytest.fixture()
def processor(monkeypatch):
    prompt_manager = FakePromptManager()
    provider = FakeProvider()
    proc = LLMProcessor(prompt_manager=prompt_manager, primary=provider, fallback=None)
    monkeypatch.setattr(proc, "_save_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(proc, "_update_project_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(proc, "_truncate_to_instagram_limit", lambda text: text)
    monkeypatch.setattr(proc, "_validate_generated_text", lambda text: None)
    monkeypatch.setattr(proc, "_call_with_retry", lambda provider, messages: (provider.complete(messages), None))
    return proc, prompt_manager, provider


def test_process_one_runs_three_analysis_types(processor):
    proc, prompt_manager, provider = processor

    status = proc.process_one({"id": 1, "sigla_tipo": "PL", "numero": 1234, "ano": 2024, "ementa_bruta": "Teste"})

    assert status == "SUCESSO"
    assert prompt_manager.calls == ["IMPARCIAL", "DIREITA", "ESQUERDA"]
    assert provider.calls == 3


def test_process_one_returns_fallback_when_any_analysis_uses_fallback(monkeypatch):
    prompt_manager = FakePromptManager()
    primary = FakeProvider()
    fallback = FakeProvider()
    proc = LLMProcessor(prompt_manager=prompt_manager, primary=primary, fallback=fallback)
    monkeypatch.setattr(proc, "_save_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(proc, "_update_project_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(proc, "_truncate_to_instagram_limit", lambda text: text)
    monkeypatch.setattr(proc, "_validate_generated_text", lambda text: None)

    def fake_call_with_retry(provider, messages):
        if provider is primary and len(prompt_manager.calls) == 1:
            return ({"text": "PL - 1234/2024\nTexto suficiente para passar na validação.", "prompt_tokens": 1, "completion_tokens": 1, "model": "primary"}, None)
        if provider is primary:
            return ({"text": "PL - 1234/2024\nTexto suficiente para passar na validação.", "prompt_tokens": 1, "completion_tokens": 1, "model": "primary"}, None)
        return ({"text": "PL - 1234/2024\nTexto suficiente para passar na validação.", "prompt_tokens": 1, "completion_tokens": 1, "model": "fallback"}, None)

    monkeypatch.setattr(proc, "_call_with_retry", fake_call_with_retry)
    monkeypatch.setattr(proc, "_process_single_analysis", lambda projeto_row, tipo_analise, texto_para_llm, processado_parcialmente: ("FALLBACK_UTILIZADO" if tipo_analise == "DIREITA" else "SUCESSO", {"text": "ok"}))

    status = proc.process_one({"id": 1, "sigla_tipo": "PL", "numero": 1234, "ano": 2024, "ementa_bruta": "Teste"})

    assert status == "FALLBACK_UTILIZADO"


def test_run_counts_processed_items(monkeypatch):
    proc = LLMProcessor(prompt_manager=FakePromptManager(), primary=FakeProvider(), fallback=None)
    monkeypatch.setattr(proc, "process_one", lambda projeto: "SUCESSO")

    fake_rows = [SimpleNamespace(id=1, id_origem="1", sigla_tipo="PL", numero=1, ano=2024, ementa_bruta="A")]

    class FakeSession:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def execute(self, *args, **kwargs):
            return SimpleNamespace(fetchall=lambda: fake_rows)

    monkeypatch.setattr("processing.llm_client.get_session", lambda: FakeSession())

    stats = proc.run(batch_size=10)
    assert stats["processados"] == 1
    assert stats["sucesso"] == 1

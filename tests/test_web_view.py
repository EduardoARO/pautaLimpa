from __future__ import annotations

from types import SimpleNamespace

from web.app import _build_view_model


def test_build_view_model_creates_three_analysis_slots():
    rows = [
        SimpleNamespace(
            id=1,
            id_origem="100",
            sigla_tipo="PL",
            numero=1234,
            ano=2024,
            ementa_bruta="Ementa",
            status_processamento="AGUARDANDO_MIDIA",
            data_apresentacao=None,
            data_captura=None,
            link_oficial=None,
            url_inteiro_teor=None,
            tipo_analise="IMPARCIAL",
            texto_traduzido="PL - 1234/2024\nTexto imparcial",
            status_ia="SUCESSO",
            modelo_llm="model-a",
            prompt_tokens=3,
            completion_tokens=7,
            data_processamento=None,
        ),
    ]

    groups = _build_view_model(rows)
    assert list(groups.keys()) == ["Sem data"]
    item = groups["Sem data"][0]
    assert [a["key"] for a in item["analysis_order"]] == ["IMPARCIAL", "DIREITA", "ESQUERDA"]
    assert item["analysis_order"][1]["texto"] == "Ainda sem texto gerado pela IA."
    assert item["analysis_order"][2]["status_ia"] == "PENDENTE"

import os
from collections import defaultdict
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

from models.database import get_database_diagnostics, get_session
from utils.logger import get_logger
from web.rag_search import rank_project_groups

load_dotenv()

app = Flask(__name__)
logger = get_logger(__name__)

_NEXT_FRONTEND_URL = os.getenv("NEXT_FRONTEND_URL", "http://127.0.0.1:3000")

_ANALYSIS_ORDER = ("IMPARCIAL", "DIREITA", "ESQUERDA")
_ANALYSIS_PLACEHOLDER = {
    "texto": "Ainda sem texto gerado pela IA.",
    "status_ia": "PENDENTE",
    "modelo_llm": "—",
    "tokens": 0,
    "data_processamento": "—",
    "caption_chars": 0,
}

_POSTS_QUERY = text("""
    WITH base AS (
        SELECT
            pb.id,
            pb.id_origem,
            pb.sigla_tipo,
            pb.numero,
            pb.ano,
            pb.ementa_bruta,
            pb.status_processamento,
            pb.data_apresentacao,
            pb.data_captura,
            pb.link_oficial,
            pb.url_inteiro_teor
        FROM projetos_brutos pb
        WHERE (:date_from = '' OR pb.data_apresentacao >= CAST(:date_from AS DATE))
          AND (:date_to = '' OR pb.data_apresentacao <= CAST(:date_to AS DATE))
        ORDER BY pb.data_apresentacao DESC NULLS LAST, pb.id DESC
    )
    SELECT
        base.id,
        base.id_origem,
        base.sigla_tipo,
        base.numero,
        base.ano,
        base.ementa_bruta,
        base.status_processamento,
        base.data_apresentacao,
        base.data_captura,
        base.link_oficial,
        base.url_inteiro_teor,
        COALESCE(ai.tipo_analise::text, 'IMPARCIAL') AS tipo_analise,
        COALESCE(ai.texto_traduzido, pia.texto_traduzido) AS texto_traduzido,
        COALESCE(ai.status_ia::text, pia.status_ia::text, 'PENDENTE') AS status_ia,
        COALESCE(ai.modelo_llm, pia.modelo_llm) AS modelo_llm,
        COALESCE(ai.prompt_tokens, pia.prompt_tokens) AS prompt_tokens,
        COALESCE(ai.completion_tokens, pia.completion_tokens) AS completion_tokens,
        COALESCE(ai.data_processamento, pia.data_processamento) AS data_processamento
    FROM base
    LEFT JOIN analises_ia ai ON ai.fk_projeto = base.id
    LEFT JOIN processamento_ia pia
        ON pia.fk_projeto = base.id
       AND ai.id IS NULL
    ORDER BY base.data_apresentacao DESC NULLS LAST, base.id DESC, tipo_analise
""")

_STATS_QUERY = text("""
    SELECT status_processamento, COUNT(*) AS total
    FROM projetos_brutos
    GROUP BY status_processamento
    ORDER BY status_processamento
""")


def _format_date(value) -> str:
    if value is None:
        return "Sem data"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    return value.strftime("%d/%m/%Y") if hasattr(value, "strftime") else str(value)


def _format_datetime(value) -> str:
    if value is None:
        return "—"
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y %H:%M")
    return str(value)


def _build_view_model(rows):
    grouped = defaultdict(list)
    projects = {}
    for row in rows:
        group_date = row.data_apresentacao
        date_label = _format_date(group_date)
        key = row.id
        if key not in projects:
            projects[key] = {
                "id": row.id,
                "id_origem": row.id_origem,
                "title": f"{row.sigla_tipo} {row.numero}/{row.ano}",
                "ementa": row.ementa_bruta,
                "status_processamento": row.status_processamento,
                "data_apresentacao": _format_date(row.data_apresentacao),
                "group_label": date_label,
                "data_captura": _format_datetime(row.data_captura),
                "link_oficial": row.link_oficial,
                "url_inteiro_teor": row.url_inteiro_teor,
                "analyses": {},
            }

        analysis_text = row.texto_traduzido or "Ainda sem texto gerado pela IA."
        total_tokens = (row.prompt_tokens or 0) + (row.completion_tokens or 0)
        projects[key]["analyses"][row.tipo_analise] = {
            "tipo_analise": row.tipo_analise,
            "texto": analysis_text,
            "status_ia": row.status_ia or "PENDENTE",
            "modelo_llm": row.modelo_llm or "—",
            "tokens": total_tokens,
            "data_processamento": _format_datetime(row.data_processamento),
            "caption_chars": len(analysis_text),
        }

    for item in projects.values():
        analyses = item["analyses"]
        for analysis_key in _ANALYSIS_ORDER:
            analyses.setdefault(analysis_key, {"tipo_analise": analysis_key, **_ANALYSIS_PLACEHOLDER})

        item["caption"] = analyses["IMPARCIAL"]["texto"]
        item["analysis_order"] = [
            {"key": key, **value}
            for key, value in (
                ("ESQUERDA", analyses["ESQUERDA"]),
                ("IMPARCIAL", analyses["IMPARCIAL"]),
                ("DIREITA", analyses["DIREITA"]),
            )
        ]
        grouped[item["group_label"]].append(item)
    return dict(grouped)


def _build_dashboard_payload(date_from: str, date_to: str, theme: str) -> tuple[dict, int]:
    with get_session() as session:
        rows = session.execute(
            _POSTS_QUERY,
            {
                "date_from": date_from,
                "date_to": date_to,
                "theme": theme,
            },
        ).fetchall()
        stats_rows = session.execute(_STATS_QUERY).fetchall()

    groups = _build_view_model(rows)
    if theme:
        try:
            groups = rank_project_groups(groups, theme)
        except RuntimeError as exc:
            logger.warning("Busca RAG indisponível no momento: %s", exc)

    stats = {row.status_processamento: row.total for row in stats_rows}
    total_visible = sum(len(items) for items in groups.values())
    return {
        "groups": groups,
        "stats": stats,
        "date_from": date_from,
        "date_to": date_to,
        "theme": theme,
        "total_visible": total_visible,
    }, total_visible


@app.route("/")
def index():
    return redirect(_NEXT_FRONTEND_URL, code=302)


@app.route("/api/dashboard")
def api_dashboard():
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""
    theme = (request.args.get("theme") or "").strip()

    try:
        payload, _ = _build_dashboard_payload(date_from, date_to, theme)
    except SQLAlchemyError as exc:
        return jsonify(
            {
                "error": "db_connection_failed",
                "message": "Não foi possível conectar ao PostgreSQL configurado.",
                "details": str(exc),
                "diagnostics": get_database_diagnostics(),
            }
        ), 500

    return jsonify(payload)


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok", "diagnostics": get_database_diagnostics()})


if __name__ == "__main__":
    app.run(
        host=os.getenv("UI_HOST", "127.0.0.1"),
        port=int(os.getenv("UI_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "true").lower() == "true",
    )

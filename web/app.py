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

_NEXT_FRONTEND_URL = os.getenv("NEXT_FRONTEND_URL", "http://127.0.0.1:3000").rstrip("/")
_ANALYSIS_PREVIEW_LIMIT = 700

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
            pb.sigla_tipo,
            pb.numero,
            pb.ano,
            pb.ementa_bruta,
            pb.data_apresentacao,
            pb.link_oficial,
            pb.url_inteiro_teor
        FROM projetos_brutos pb
        WHERE (:date_from = '' OR pb.data_apresentacao >= CAST(:date_from AS DATE))
          AND (:date_to = '' OR pb.data_apresentacao <= CAST(:date_to AS DATE))
        ORDER BY pb.data_apresentacao DESC NULLS LAST, pb.id DESC
    )
    SELECT
        base.id,
        base.sigla_tipo,
        base.numero,
        base.ano,
        base.ementa_bruta,
        base.data_apresentacao,
        base.link_oficial,
        base.url_inteiro_teor,
        COALESCE(ai.tipo_analise::text, 'IMPARCIAL') AS tipo_analise,
        LEFT(COALESCE(ai.texto_traduzido, pia.texto_traduzido, ''), :preview_limit) AS texto_preview,
        COALESCE(LENGTH(COALESCE(ai.texto_traduzido, pia.texto_traduzido, '')) > :preview_limit, FALSE) AS has_more
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

_ANALYSIS_TEXT_QUERY = text("""
    SELECT
        COALESCE(ai.texto_traduzido, pia.texto_traduzido) AS texto_traduzido,
        COALESCE(ai.tipo_analise::text, 'IMPARCIAL') AS tipo_analise
    FROM projetos_brutos pb
    LEFT JOIN analises_ia ai
        ON ai.fk_projeto = pb.id
       AND ai.tipo_analise::text = :tipo_analise
    LEFT JOIN processamento_ia pia
        ON pia.fk_projeto = pb.id
       AND ai.id IS NULL
    WHERE pb.id = :project_id
      AND COALESCE(ai.tipo_analise::text, 'IMPARCIAL') = :tipo_analise
    LIMIT 1
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
                "title": f"{row.sigla_tipo} {row.numero}/{row.ano}",
                "ementa": row.ementa_bruta,
                "data_apresentacao": _format_date(row.data_apresentacao),
                "group_label": date_label,
                "link_oficial": row.link_oficial,
                "url_inteiro_teor": row.url_inteiro_teor,
            }

        preview_text = row.texto_preview or _ANALYSIS_PLACEHOLDER["texto"]
        has_more = bool(row.has_more)
        projects[key].setdefault("analysis_order", [])
        projects[key]["analysis_order"].append(
            {
                "key": row.tipo_analise,
                "texto": preview_text,
                "has_more": has_more,
            }
        )

    for item in projects.values():
        analyses = {entry["key"]: entry for entry in item.get("analysis_order", [])}
        item["analysis_order"] = [
            analyses.get(
                analysis_key,
                {
                    "key": analysis_key,
                    "texto": _ANALYSIS_PLACEHOLDER["texto"],
                    "has_more": False,
                },
            )
            for analysis_key in _ANALYSIS_ORDER
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
                "preview_limit": _ANALYSIS_PREVIEW_LIMIT,
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


@app.after_request
def add_cors_headers(response):
    if request.path.startswith("/api/"):
        response.headers["Access-Control-Allow-Origin"] = _NEXT_FRONTEND_URL
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


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


@app.route("/api/analysis-text")
def api_analysis_text():
    project_id = request.args.get("project_id", type=int)
    tipo_analise = (request.args.get("tipo_analise") or "").strip().upper()

    if project_id is None:
        return jsonify({"error": "project_id_required", "message": "project_id é obrigatório."}), 400

    if tipo_analise not in _ANALYSIS_ORDER:
        return (
            jsonify(
                {
                    "error": "invalid_tipo_analise",
                    "message": "tipo_analise deve ser IMPARCIAL, DIREITA ou ESQUERDA.",
                }
            ),
            400,
        )

    with get_session() as session:
        row = session.execute(
            _ANALYSIS_TEXT_QUERY,
            {"project_id": project_id, "tipo_analise": tipo_analise},
        ).mappings().first()

    if not row or not row.get("texto_traduzido"):
        return (
            jsonify(
                {
                    "error": "analysis_not_found",
                    "message": "Não foi possível localizar o texto completo da análise solicitada.",
                }
            ),
            404,
        )

    return jsonify(
        {
            "project_id": project_id,
            "tipo_analise": row["tipo_analise"],
            "texto": row["texto_traduzido"],
        }
    )


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok", "diagnostics": get_database_diagnostics()})


if __name__ == "__main__":
    app.run(
        host=os.getenv("UI_HOST", "127.0.0.1"),
        port=int(os.getenv("UI_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "true").lower() == "true",
    )

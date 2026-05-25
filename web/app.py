import os
from collections import defaultdict
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, render_template, request
from sqlalchemy import text

from models.database import get_session

load_dotenv()

app = Flask(__name__)

_POSTS_QUERY = text("""
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
        pb.url_inteiro_teor,
        pia.texto_traduzido,
        pia.status_ia,
        pia.modelo_llm,
        pia.prompt_tokens,
        pia.completion_tokens,
        pia.data_processamento
    FROM projetos_brutos pb
    LEFT JOIN processamento_ia pia ON pia.fk_projeto = pb.id
    WHERE (:status = 'TODOS' OR pb.status_processamento::text = :status)
    ORDER BY pb.data_apresentacao DESC NULLS LAST, pb.id DESC
    LIMIT :limit
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
    for row in rows:
        group_date = row.data_apresentacao
        date_label = _format_date(group_date)
        total_tokens = (row.prompt_tokens or 0) + (row.completion_tokens or 0)
        if row.status_processamento == "ERRO_CAPTURA":
            caption = "Este projeto entrou em ERRO_CAPTURA porque a ementa ficou insuficiente após a sanitização. Ele não será enviado para a IA até revisão ou nova captura."
        else:
            caption = row.texto_traduzido or "Ainda sem texto gerado pela IA."
        item = {
            "id": row.id,
            "id_origem": row.id_origem,
            "title": f"{row.sigla_tipo} {row.numero}/{row.ano}",
            "ementa": row.ementa_bruta,
            "caption": caption,
            "status_processamento": row.status_processamento,
            "status_ia": row.status_ia or "PENDENTE",
            "modelo_llm": row.modelo_llm or "—",
            "tokens": total_tokens,
            "data_apresentacao": _format_date(row.data_apresentacao),
            "data_captura": _format_datetime(row.data_captura),
            "data_processamento": _format_datetime(row.data_processamento),
            "link_oficial": row.link_oficial,
            "url_inteiro_teor": row.url_inteiro_teor,
            "caption_chars": len(caption),
        }
        grouped[date_label].append(item)
    return dict(grouped)


@app.route("/")
def index():
    limit = min(int(request.args.get("limit", os.getenv("UI_POSTS_LIMIT", "50"))), 200)
    status = request.args.get("status", "TODOS")

    with get_session() as session:
        rows = session.execute(_POSTS_QUERY, {"limit": limit, "status": status}).fetchall()
        stats_rows = session.execute(_STATS_QUERY).fetchall()

    groups = _build_view_model(rows)
    stats = {row.status_processamento: row.total for row in stats_rows}

    return render_template(
        "index.html",
        groups=groups,
        stats=stats,
        selected_status=status,
        limit=limit,
        total_visible=sum(len(items) for items in groups.values()),
    )


if __name__ == "__main__":
    app.run(
        host=os.getenv("UI_HOST", "127.0.0.1"),
        port=int(os.getenv("UI_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "true").lower() == "true",
    )

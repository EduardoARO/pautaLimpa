import os
import textwrap

from dotenv import load_dotenv
from sqlalchemy import text

from models.database import get_session

load_dotenv()

_LIMIT = int(os.getenv("PREVIEW_POSTS_LIMIT", "5"))

_QUERY = text("""
    SELECT
        pb.id,
        pb.id_origem,
        pb.sigla_tipo,
        pb.numero,
        pb.ano,
        pb.ementa_bruta,
        pb.status_processamento,
        pia.texto_traduzido,
        pia.status_ia,
        pia.modelo_llm,
        pia.prompt_tokens,
        pia.completion_tokens,
        pia.data_processamento
    FROM projetos_brutos pb
    JOIN processamento_ia pia ON pia.fk_projeto = pb.id
    WHERE pia.status_ia IN ('SUCESSO', 'FALLBACK_UTILIZADO')
    ORDER BY pia.data_processamento DESC
    LIMIT :limit
""")


def _divider() -> str:
    return "=" * 100


def _wrap(text_value: str, width: int = 100) -> str:
    return "\n".join(textwrap.wrap(text_value or "", width=width, replace_whitespace=False))


def main() -> None:
    with get_session() as session:
        rows = session.execute(_QUERY, {"limit": _LIMIT}).fetchall()

    if not rows:
        print("Nenhum post processado pela IA encontrado ainda.")
        print("Rode primeiro: python -m processing.llm_client")
        return

    for row in rows:
        total_tokens = (row.prompt_tokens or 0) + (row.completion_tokens or 0)
        titulo = f"{row.sigla_tipo} {row.numero}/{row.ano} | DB id={row.id} | origem={row.id_origem}"

        print(_divider())
        print(titulo)
        print(f"Status projeto: {row.status_processamento}")
        print(f"Status IA: {row.status_ia} | Modelo: {row.modelo_llm} | Tokens: {total_tokens}")
        print(f"Processado em: {row.data_processamento}")
        print("-" * 100)
        print("EMENTA ORIGINAL:")
        print(_wrap(row.ementa_bruta))
        print("-" * 100)
        print("TEXTO DO POST / CAPTION:")
        print(row.texto_traduzido or "")
        print(_divider())
        print()


if __name__ == "__main__":
    main()

"""Reset local do banco de dados do PautaLimpa.

ATENÇÃO: este script remove todos os registros das tabelas do aplicativo e
redefine as sequências. Use apenas no ambiente local.

Uso:
    py -3 reset_local_db.py --confirm
"""

from __future__ import annotations

import argparse

from sqlalchemy import text

from models.database import get_session, check_connection
from update_prompt import main as seed_prompts

TABLES_IN_ORDER = [
    "metricas_engajamento",
    "logs_publicacao",
    "auditoria",
    "publicacoes_instagram",
    "analises_ia",
    "processamento_ia",
    "projetos_brutos",
    "historico_prompt",
]


def reset_database(confirm: bool) -> None:
    if not confirm:
        raise SystemExit("Confirmação ausente. Reexecute com --confirm para apagar os registros.")

    if not check_connection():
        raise SystemExit("Não foi possível conectar ao banco. Abortando o reset.")

    with get_session() as session:
        for table in TABLES_IN_ORDER:
            session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
        session.commit()

    seed_prompts()
    print("Banco local limpo e prompts reativados com sucesso.")
    print("Agora rode:")
    print("  py -3 run_local.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Limpa todos os registros do banco local do PautaLimpa.")
    parser.add_argument("--confirm", action="store_true", help="Confirma a limpeza total do banco")
    args = parser.parse_args()
    reset_database(args.confirm)

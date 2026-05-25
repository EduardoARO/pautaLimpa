from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from models.database import get_database_diagnostics, get_session


def main() -> None:
    print("Diagnóstico de configuração:")
    diagnostics = get_database_diagnostics()
    for key, value in diagnostics.items():
        print(f"- {key}: {value}")

    try:
        with get_session() as session:
            result = session.execute(text("SELECT 1 AS ok")).scalar_one()
        print(f"\nConexão OK: {result}")
    except SQLAlchemyError as exc:
        print("\nErro ao conectar:")
        print(exc)


if __name__ == "__main__":
    main()

import os

import requests
from dotenv import load_dotenv
from sqlalchemy import text

from models.database import get_session

load_dotenv()

_BASE_URL = os.getenv("API_BASE_URL", "https://dadosabertos.camara.leg.br/api/v2")

_FETCH_ONE_SQL = text("""
    SELECT id, id_origem, sigla_tipo, numero, ano, data_apresentacao, ementa_bruta
    FROM projetos_brutos
    ORDER BY data_apresentacao DESC NULLS LAST, id DESC
    LIMIT 1
""")


def main() -> None:
    with get_session() as session:
        row = session.execute(_FETCH_ONE_SQL).fetchone()

    if not row:
        print("Nenhum projeto encontrado no banco.")
        return

    response = requests.get(
        f"{_BASE_URL}/proposicoes/{row.id_origem}",
        headers={"Accept": "application/json", "User-Agent": "PautaLimpa-Bot/1.0"},
        timeout=20,
    )
    response.raise_for_status()
    dados = response.json().get("dados", {})

    print("Registro verificado")
    print(f"- Banco ID: {row.id}")
    print(f"- Projeto: {row.sigla_tipo} {row.numero}/{row.ano}")
    print(f"- ID Câmara: {row.id_origem}")
    print(f"- Data no banco: {row.data_apresentacao}")
    print(f"- Data na API Câmara: {dados.get('dataApresentacao')}")
    print(f"- Ementa: {row.ementa_bruta}")
    print(f"- URL API: {_BASE_URL}/proposicoes/{row.id_origem}")


if __name__ == "__main__":
    main()

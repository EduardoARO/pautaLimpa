"""backend/app.py

Ponto de entrada oficial da API Flask do PautaLimpa.
Esta camada reaproveita a implementação atual enquanto a migração do projeto
é organizada para /backend e /frontend.
"""

from __future__ import annotations

from web.app import app, api_dashboard, api_health

__all__ = ["app", "api_dashboard", "api_health"]


if __name__ == "__main__":
    import os

    app.run(
        host=os.getenv("UI_HOST", "127.0.0.1"),
        port=int(os.getenv("UI_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "true").lower() == "true",
    )

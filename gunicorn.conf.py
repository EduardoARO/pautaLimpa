"""Configuração do Gunicorn carregada automaticamente no deploy (Render).

O Gunicorn lê este arquivo mesmo quando o start command é apenas
`gunicorn backend.wsgi:application`, garantindo bind e timeout corretos
independentemente do que estiver configurado no painel do Render.
"""

import os

# Render injeta a porta via $PORT; o bind precisa ser 0.0.0.0 para o port scan.
bind = f"0.0.0.0:{os.getenv('PORT', '10000')}"

# Plano free tem pouca memória: 1 worker + threads evita OOM/SIGKILL.
workers = int(os.getenv("WEB_CONCURRENCY", "1"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))
worker_class = "gthread"

# A consulta do dashboard pode levar alguns segundos; o default de 30s mata o worker.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "120"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

# Logs no stdout para aparecerem no painel do Render.
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")

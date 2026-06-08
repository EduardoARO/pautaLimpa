import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/pautalimpa_test")
os.environ.setdefault("FLASK_DEBUG", "false")

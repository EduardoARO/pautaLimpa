"""Launcher local único do PautaLimpa.

Sobe o backend Flask (API), o frontend Next.js e o scheduler de ingestão/processamento em processos separados.
Uso:
    py -3 run_local.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SERVICES = (
    ("backend", [sys.executable, "-m", "backend.app"], {"FLASK_DEBUG": "false", "UI_HOST": "127.0.0.1", "UI_PORT": "5000"}),
    ("frontend", ["cmd", "/c", "npm", "--prefix", "frontend", "run", "dev"], {}),
    ("scheduler", [sys.executable, "-m", "pipeline.scheduler"], {}),
)


def _spawn_service(name: str, command: list[str], extra_env: dict[str, str]) -> subprocess.Popen:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("FLASK_DEBUG", "false")
    env.setdefault("NEXT_FRONTEND_URL", "http://127.0.0.1:3000")
    env.setdefault("BACKEND_URL", "http://127.0.0.1:5000")
    env.update(extra_env)
    print(f"[PautaLimpa] iniciando {name}: {' '.join(command)}")
    return subprocess.Popen(command, cwd=str(ROOT), env=env)


def main() -> int:
    processes: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, command, extra_env in SERVICES:
            processes.append((name, _spawn_service(name, command, extra_env)))

        print("[PautaLimpa] serviços ativos:")
        print("- Frontend Next: http://127.0.0.1:3000")
        print("- Backend Flask API: http://127.0.0.1:5000")
        print("- Scheduler: execução contínua em segundo plano")
        print("- Para sair: Ctrl+C")

        while True:
            for name, proc in processes:
                return_code = proc.poll()
                if return_code is not None:
                    raise RuntimeError(f"Serviço '{name}' encerrou com código {return_code}.")
            time.sleep(1)
    except KeyboardInterrupt:
        print("[PautaLimpa] encerrando serviços...")
    finally:
        for _, proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for _, proc in processes:
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

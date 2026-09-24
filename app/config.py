"""Configuração do app.

No build (build.ps1) os valores são gravados em app/_build_config.py.
Rodando do código-fonte, lê variáveis de ambiente ou o .env.local na raiz do projeto.
"""

import os
import sys
from pathlib import Path

try:
    from . import _build_config as _built
except ImportError:
    _built = None


def _read_env_file() -> dict[str, str]:
    env_file = Path(__file__).resolve().parents[1] / ".env.local"
    values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


_env = {**_read_env_file(), **os.environ}


def _get(name: str, default: str = "") -> str:
    if _built is not None and getattr(_built, name, None):
        return getattr(_built, name)
    return _env.get(f"VITE_{name}") or _env.get(name) or default


SUPABASE_URL = _get("SUPABASE_URL").rstrip("/")
SUPABASE_ANON_KEY = _get("SUPABASE_ANON_KEY")
TURN_URL = _get("TURN_URL")
TURN_USERNAME = _get("TURN_USERNAME")
TURN_CREDENTIAL = _get("TURN_CREDENTIAL")

# Sala única e permanente: todo mundo que abre o app entra nela.
ROOM_CODE = _get("ROOM_CODE", "SALA").upper()

WEB_URL = _get("WEB_URL", "https://shared-screen-seven.vercel.app").rstrip("/")
GITHUB_REPO = _get("GITHUB_REPO", "weldermarques/shared-screen-desktop")

FROZEN = getattr(sys, "frozen", False)
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SharedScreen"

"""Preferências do usuário (settings.json em %LOCALAPPDATA%\\SharedScreen)."""

from __future__ import annotations

import json
import logging
import os

from . import config

log = logging.getLogger(__name__)

_FILE = config.DATA_DIR / "settings.json"
MAX_NAME = 24


def _load() -> dict:
    try:
        return json.loads(_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001 - arquivo corrompido: volta ao padrão
        log.warning("settings.json inválido; usando padrões", exc_info=True)
        return {}


def _save(data: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_name() -> str:
    """Nome mostrado na lista da sala; padrão: o usuário do Windows."""
    name = _load().get("name") or os.environ.get("USERNAME") or "Convidado"
    return name.strip()[:MAX_NAME] or "Convidado"


def set_name(name: str) -> str:
    name = name.strip()[:MAX_NAME]
    data = _load()
    data["name"] = name
    _save(data)
    return get_name()

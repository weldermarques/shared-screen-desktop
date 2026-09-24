"""Grava app/_build_config.py com a versão e a configuração embutidas no executável.

Lê de variáveis de ambiente (SUPABASE_URL ou VITE_SUPABASE_URL etc.) ou do .env.local na raiz do projeto.
"""

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYS = ["SUPABASE_URL", "SUPABASE_ANON_KEY", "TURN_URL", "TURN_USERNAME", "TURN_CREDENTIAL", "WEB_URL", "GITHUB_REPO", "ROOM_CODE"]


def read_env_file() -> dict[str, str]:
    env_file = ROOT / ".env.local"
    values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    env = {**read_env_file(), **os.environ}
    lines = [f"VERSION = {args.version.lstrip('vV')!r}"]
    for key in KEYS:
        value = env.get(key) or env.get(f"VITE_{key}") or ""
        lines.append(f"{key} = {value!r}")

    missing = [k for k in ("SUPABASE_URL", "SUPABASE_ANON_KEY") if not (env.get(k) or env.get(f"VITE_{k}"))]
    if missing:
        raise SystemExit(f"Faltando configuração: {', '.join(missing)}")

    (ROOT / "app" / "_build_config.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"app/_build_config.py gravado (versão {args.version})")


if __name__ == "__main__":
    main()

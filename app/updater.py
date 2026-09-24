"""Atualização obrigatória via GitHub Releases.

Toda release publicada com tag vX.Y.Z e um asset SharedScreen-Setup-*.exe é considerada
obrigatória: versões anteriores não conseguem usar o app até instalar a nova.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx
from packaging.version import InvalidVersion, Version

from . import __version__, config

log = logging.getLogger(__name__)


@dataclass
class Release:
    version: str
    download_url: str
    asset_name: str
    size: int
    notes: str


def _parse(version: str) -> Version | None:
    try:
        return Version(version.lstrip("vV"))
    except InvalidVersion:
        return None


def is_newer(latest: str, current: str = __version__) -> bool:
    new, cur = _parse(latest), _parse(current)
    return bool(new and cur and new > cur)


def update_checks_enabled() -> bool:
    # Rodando do código-fonte (versão dev) não força update.
    return config.FROZEN and _parse(__version__) is not None and "dev" not in __version__


async def fetch_latest(timeout: float = 8) -> Release | None:
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": f"SharedScreen/{__version__}"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            return None  # nenhuma release publicada ainda
        resp.raise_for_status()
        data = resp.json()

    asset = next(
        (
            a
            for a in data.get("assets", [])
            if a["name"].lower().endswith(".exe") and "setup" in a["name"].lower()
        ),
        None,
    )
    if not asset:
        return None
    return Release(
        version=data["tag_name"].lstrip("vV"),
        download_url=asset["browser_download_url"],
        asset_name=asset["name"],
        size=asset.get("size", 0),
        notes=data.get("body") or "",
    )


async def check_for_update() -> Release | None:
    """Retorna a release se houver versão mais nova; None se estiver atualizado."""
    if not update_checks_enabled():
        return None
    release = await fetch_latest()
    if release and is_newer(release.version):
        log.info("atualização disponível: %s -> %s", __version__, release.version)
        return release
    return None


async def download(release: Release, on_progress: Callable[[int, int], None]) -> Path:
    dest = Path(tempfile.gettempdir()) / release.asset_name
    tmp = dest.with_suffix(".part")
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=60), follow_redirects=True) as client:
        async with client.stream("GET", release.download_url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or release.size or 0)
            done = 0
            with tmp.open("wb") as fh:
                async for chunk in resp.aiter_bytes(256 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    on_progress(done, total)
    if total and tmp.stat().st_size != total:
        raise IOError("Download incompleto.")
    tmp.replace(dest)
    return dest


def launch_installer(installer: Path) -> None:
    """Roda o instalador em modo silencioso; ele fecha este app e o reabre ao terminar."""
    subprocess.Popen(
        [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        close_fds=True,
    )

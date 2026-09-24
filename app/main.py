from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import qasync
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout

from . import APP_NAME, __version__, config, updater
from .ui import theme
from .ui.update_dialog import UpdateDialog
from .ui.widgets import label

log = logging.getLogger("shared_screen")


def setup_logging() -> None:
    log_dir = config.DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    ]
    if not config.FROZEN:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers
    )
    for noisy in ("aioice", "aiortc", "httpx", "websockets", "realtime"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "assets" / name


class Splash(QDialog):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.setWindowFlags(Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        self.setFixedSize(320, 120)
        layout = QVBoxLayout(self)
        title = label(f"🖥️ {APP_NAME}", "h2")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        msg = label(text, "muted")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(msg)
        self.setStyleSheet("QDialog { border: 1px solid #2a3140; }")


async def run(app: QApplication) -> None:
    quit_event = asyncio.Event()
    app.aboutToQuit.connect(quit_event.set)

    release = None
    if updater.update_checks_enabled():
        splash = Splash("Verificando atualizações…")
        splash.show()
        try:
            release = await asyncio.wait_for(updater.check_for_update(), timeout=10)
        except Exception:  # noqa: BLE001 - sem internet / rate limit: segue e tenta de novo depois
            log.warning("não foi possível verificar atualizações", exc_info=True)
        splash.close()

    windows = []
    if release:
        dialog = UpdateDialog(release)
        dialog.show()
        dialog.start()
        windows.append(dialog)
    else:
        from .ui.main_window import MainWindow

        window = MainWindow()
        window.show()
        windows.append(window)

    await quit_event.wait()


async def selftest() -> bool:
    """Transmite para si mesmo pelo Supabase e confere se chegam vídeo e áudio."""
    from .rtc import HostSession, ViewerSession, random_code

    code = random_code()
    host = HostSession(code, 1, True, on_stats=lambda v, c: None)
    frames: list = []
    audio: list[bool] = []
    viewer = ViewerSession(code, lambda s: log.info("selftest status: %s", s), frames.append, audio.append)
    try:
        await host.start()
        await viewer.start()
        for _ in range(40):
            if len(frames) >= 10:
                break
            await asyncio.sleep(0.5)
    finally:
        await viewer.close()
        await host.stop()
    ok = len(frames) >= 10
    log.info("selftest: %s (frames=%d, audio_host=%s, audio_viewer=%s)", "OK" if ok else "FALHOU", len(frames), host.include_audio, any(audio))
    return ok


def main() -> None:
    setup_logging()
    log.info("iniciando %s %s", APP_NAME, __version__)

    if "--selftest" in sys.argv:
        sys.exit(0 if asyncio.run(selftest()) else 1)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(theme.STYLE)
    icon = resource_path("icon.ico")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    try:
        qasync.run(run(app))
    except (asyncio.CancelledError, RuntimeError):
        pass


if __name__ == "__main__":
    main()

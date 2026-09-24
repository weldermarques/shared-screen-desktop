from __future__ import annotations

import re
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from .. import __version__
from .widgets import button, card, label


class HomePage(QWidget):
    def __init__(self, on_host: Callable[[], None], on_watch: Callable[[str], None]) -> None:
        super().__init__()
        self.setObjectName("page")
        self._on_watch = on_watch

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 20)
        root.addStretch()

        title = label("🖥️ Shared Screen", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)
        subtitle = label("Compartilhe sua tela ou assista a uma transmissão.", "muted")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(subtitle)
        root.addSpacing(24)

        row = QHBoxLayout()
        row.setSpacing(16)
        row.addStretch()

        host_card, host = card()
        host_card.setFixedWidth(340)
        host.addWidget(label("Apresentar", "h2"))
        host.addWidget(label("Gere um código e transmita um dos seus monitores, com ou sem áudio.", "muted", wrap=True))
        host.addStretch()
        host_btn = button("Compartilhar minha tela", "big")
        host_btn.clicked.connect(on_host)
        host.addWidget(host_btn)
        row.addWidget(host_card)

        watch_card, watch = card()
        watch_card.setFixedWidth(340)
        watch.addWidget(label("Assistir", "h2"))
        watch.addWidget(label("Digite o código recebido (funciona com transmissões do site também).", "muted", wrap=True))
        self.code_input = QLineEdit()
        self.code_input.setObjectName("codeInput")
        self.code_input.setPlaceholderText("EX: K7P2QX")
        self.code_input.setMaxLength(12)
        self.code_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_input.textEdited.connect(self._normalize)
        self.code_input.returnPressed.connect(self._watch)
        watch.addWidget(self.code_input)
        self.watch_btn = button("Entrar", "big")
        self.watch_btn.setObjectName("secondary")
        self.watch_btn.setEnabled(False)
        self.watch_btn.clicked.connect(self._watch)
        watch.addWidget(self.watch_btn)
        row.addWidget(watch_card)

        row.addStretch()
        root.addLayout(row)
        root.addStretch()

        version = label(f"versão {__version__}", "hint")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(version)

    def _normalize(self, text: str) -> None:
        clean = self._clean(text)
        if clean != text:
            self.code_input.setText(clean)
        self.watch_btn.setEnabled(bool(clean))

    @staticmethod
    def _clean(text: str) -> str:
        # Aceita também o link inteiro (…/r/CODIGO).
        match = re.search(r"/r/([A-Za-z0-9]+)", text)
        if match:
            text = match.group(1)
        return re.sub(r"[^A-Z0-9]", "", text.upper())

    def _watch(self) -> None:
        code = self._clean(self.code_input.text())
        if code:
            self._on_watch(code)

BG = "#0d0f14"
SURFACE = "#161a22"
SURFACE_2 = "#1e2430"
BORDER = "#2a3140"
TEXT = "#e8ebf1"
MUTED = "#8b93a5"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#7aa2ff"
DANGER = "#ef4d5a"
LIVE = "#ff4d4f"

STYLE = f"""
* {{ font-family: 'Segoe UI', Inter, sans-serif; font-size: 10pt; color: {TEXT}; }}
QMainWindow, QDialog, QWidget#page {{ background: {BG}; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#bar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 22pt; font-weight: 700; }}
QLabel#h2 {{ font-size: 13pt; font-weight: 600; }}
QLabel#muted, QLabel#hint {{ color: {MUTED}; }}
QLabel#hint {{ font-size: 9pt; }}
QLabel#label {{ color: {MUTED}; font-size: 8pt; letter-spacing: 1px; }}
QLabel#code {{ font-family: Consolas, monospace; font-size: 26pt; font-weight: 700; letter-spacing: 6px; }}
QLabel#live {{ color: {LIVE}; font-weight: 700; }}
QLabel#error {{ color: {DANGER}; }}
QLabel#stat {{ background: {SURFACE_2}; border-radius: 10px; padding: 8px 12px; }}
QLabel#warn {{ background: rgba(239,77,90,0.15); color: #ffb3b9; border-radius: 10px; padding: 6px 10px; }}
QLabel#info {{ background: {SURFACE_2}; border-radius: 10px; padding: 6px 10px; }}
QPushButton {{
  background: {ACCENT}; color: white; border: 0; border-radius: 10px;
  padding: 9px 18px; font-weight: 600;
}}
QPushButton:hover {{ background: {ACCENT_HOVER}; }}
QPushButton:disabled {{ background: {SURFACE_2}; color: {MUTED}; }}
QPushButton#secondary {{ background: {SURFACE_2}; border: 1px solid {BORDER}; color: {TEXT}; }}
QPushButton#secondary:hover {{ background: {BORDER}; }}
QPushButton#danger {{ background: {DANGER}; }}
QPushButton#danger:hover {{ background: #ff6b76; }}
QPushButton#link {{ background: transparent; color: {MUTED}; padding: 4px 0; text-align: left; }}
QPushButton#link:hover {{ color: {TEXT}; }}
QPushButton#big {{ padding: 13px 20px; font-size: 11pt; }}
QLineEdit, QComboBox {{
  background: {BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px 10px;
  selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit#codeInput {{ font-family: Consolas, monospace; font-size: 18pt; letter-spacing: 4px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER}; selection-background-color: {SURFACE_2}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER}; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QProgressBar {{ background: {SURFACE_2}; border: 0; border-radius: 6px; height: 12px; text-align: center; font-size: 8pt; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 6px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: white; }}
QTextBrowser {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; }}
"""

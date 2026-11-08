"""Dark slate theme with an amber accent. Actor colours stay distinct."""

from __future__ import annotations

QSS = """
QWidget {
    background: #14171d;
    color: #e7e9ee;
    font-size: 13px;
}
QMainWindow, QDialog, QScrollArea, QFrame#panel {
    background: #14171d;
}
QFrame#strip, QFrame#composer, QFrame#legend {
    background: #1b1f27;
    border: 1px solid #2e3440;
    border-radius: 8px;
}
QLabel#muted, QLabel#meta {
    color: #9aa3b2;
    background: transparent;
}
QLabel#title {
    font-size: 16px;
    font-weight: 600;
    background: transparent;
}
QLabel#warning {
    color: #f0a3a8;
    background: transparent;
}
QPushButton {
    background: #2a3140;
    border: 1px solid #3c4558;
    border-radius: 6px;
    padding: 6px 12px;
}
QPushButton:hover { background: #343d4e; }
QPushButton:disabled { color: #6d7582; background: #22262e; }
QPushButton#primary {
    background: #8a5a2a;
    border-color: #c9843e;
    color: #fff8ef;
}
QPushButton#primary:hover { background: #a56b32; }
QPushButton#danger {
    background: #6b3036;
    border-color: #a85058;
}
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {
    background: #101318;
    border: 1px solid #343c4c;
    border-radius: 6px;
    padding: 4px 6px;
    selection-background-color: #8a5a2a;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #1b1f27;
    color: #e7e9ee;
    selection-background-color: #8a5a2a;
    selection-color: #fff8ef;
    border: 1px solid #3c4558;
    outline: 0;
}
QPlainTextEdit#composerEdit { padding: 8px; }
QTableWidget {
    background: #12151b;
    gridline-color: #2a303b;
    alternate-background-color: #181c24;
    selection-background-color: #3a3328;
}
QHeaderView::section {
    background: #1e232c;
    color: #c5cad3;
    border: none;
    padding: 6px;
}
QTabWidget::pane { border: 1px solid #2e3440; border-radius: 8px; }
QTabBar::tab {
    background: #1b1f27;
    padding: 8px 14px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}
QTabBar::tab:selected { background: #2c241c; color: #f0d3a8; }
QScrollBar:vertical { background: #14171d; width: 10px; }
QScrollBar::handle:vertical { background: #3a4150; border-radius: 4px; }
QStatusBar { background: #101318; color: #9aa3b2; }
QMenuBar { background: #1b1f27; color: #e7e9ee; }
QMenuBar::item:selected { background: #2c241c; }
QMenu {
    background: #1b1f27;
    color: #e7e9ee;
    border: 1px solid #3c4558;
}
QMenu::item { padding: 6px 22px; background: transparent; }
QMenu::item:selected { background: #8a5a2a; color: #fff8ef; }
QMenu::item:disabled { color: #6d7582; }
QListWidget {
    background: #12151b;
    color: #e7e9ee;
    border: 1px solid #2e3440;
    border-radius: 6px;
}
QListWidget::item:selected { background: #3a3328; color: #fff8ef; }
QCheckBox { background: transparent; spacing: 6px; }
QFrame#bubble {
    border-radius: 8px;
    border: 1px solid #2a303b;
    border-left-width: 4px;
}
QFrame#bubble[role="user"] { background: #1c2838; border-left-color: #8eb6ff; }
QFrame#bubble[role="local_llm"] { background: #182820; border-left-color: #8fceb0; }
QFrame#bubble[role="grok"] { background: #2a2233; border-left-color: #d7a6f5; }
QFrame#bubble[role="relay"] { background: #1c2838; border-left-color: #6f93cc; }
QFrame#bubble[role="summary"] { background: #2c2618; border-left-color: #e0b15a; }
QFrame#bubble[role="conclusion"] { background: #2c2218; border-left-color: #e09a62; }
QFrame#bubble[role="command_result"] { background: #241c1c; border-left-color: #e07a72; }
QFrame#bubble[role="error"] { background: #301c1c; border-left-color: #f07178; }
QFrame#bubble[role="review"], QFrame#bubble[role="verification"] {
    background: #2a2233; border-left-color: #d7a6f5;
}
QFrame#bubble[role="refusal"] { background: #301c28; border-left-color: #e07a9a; }
QFrame#bubble[role="system"] { background: #1c1f26; border-left-color: #8b93a7; }
QFrame#bubble QPlainTextEdit {
    background: transparent;
    border: none;
    color: #e7e9ee;
}
QLabel#dotUser { color: #8eb6ff; background: transparent; }
QLabel#dotLocal { color: #8fceb0; background: transparent; }
QLabel#dotGrok { color: #d7a6f5; background: transparent; }
QLabel#dotHost { color: #e07a72; background: transparent; }
"""


def apply_theme(app) -> None:
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)

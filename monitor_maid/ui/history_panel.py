"""Checkbox history browser for questions, answers, summaries, and prompts."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from monitor_maid.grok_build import strip_grok_status
from monitor_maid.models import HistoryItem

_FILTERS = (
    ("question", "Questions"),
    ("answer", "Answers"),
    ("summary", "Summaries"),
    ("prompt", "Prompts"),
    ("grok", "Grok"),
)


class HistoryPanel(QWidget):
    new_from_checked = pyqtSignal()
    view_item = pyqtSignal(object)
    # A row was clicked, or the current row moved from the keyboard.
    item_activated = pyqtSignal(object)
    # Kind filters changed the visible rows. set_items does not emit this.
    rebuilt = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._items: list[HistoryItem] = []
        self._checked: set[str] = set()
        self._quiet_rebuild = False
        self.can_start = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        filters = QHBoxLayout()
        self._boxes: dict[str, QCheckBox] = {}
        for key, label in _FILTERS:
            box = QCheckBox(label)
            box.setChecked(True)
            box.toggled.connect(self._rebuild)
            self._boxes[key] = box
            filters.addWidget(box)
        filters.addStretch(1)
        layout.addLayout(filters)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "Kind", "When", "Preview"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        # Per pixel, so a row can sit in the middle instead of snapping to a page of rows.
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.currentCellChanged.connect(self._on_current_cell)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.all_button = QPushButton("Select shown")
        self.none_button = QPushButton("Clear")
        buttons.addWidget(self.all_button)
        buttons.addWidget(self.none_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        ask = QHBoxLayout()
        self.ask_local = QPushButton("Send to local")
        ask.addWidget(self.ask_local)
        ask.addStretch(1)
        layout.addLayout(ask)
        self.all_button.clicked.connect(self._select_shown)
        self.none_button.clicked.connect(self._clear)

    def set_items(self, items: list[HistoryItem]) -> None:
        self._items = list(items)
        live = {item.key for item in items}
        self._checked &= live
        self._quiet_rebuild = True
        self._rebuild()
        self._quiet_rebuild = False

    def row_for_key(self, key: str) -> int:
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell is not None and cell.data(Qt.ItemDataRole.UserRole) == key:
                return row
        return -1

    def scroll_to_key(self, key: str) -> bool:
        """Select `key` and put that row in the middle of the pane when it fits.

        Signals are blocked so choosing the row from the chat does not click
        the row again and scroll the chat.
        """
        row = self.row_for_key(key)
        if row < 0:
            return False
        self.table.blockSignals(True)
        self.table.selectRow(row)
        self.table.blockSignals(False)
        self._center_row(row)
        return True

    def _center_row(self, row: int) -> None:
        index = self.table.model().index(row, 1)
        self.table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def item_at(self, pos) -> HistoryItem | None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return None
        return self.item_for_row(row)

    def item_for_row(self, row: int) -> HistoryItem | None:
        cell = self.table.item(row, 0)
        if cell is None:
            return None
        key = cell.data(Qt.ItemDataRole.UserRole)
        for item in self._items:
            if item.key == key:
                return item
        return None

    def build_context_menu(self, item: HistoryItem | None) -> QMenu:
        menu = QMenu(self)
        start = menu.addAction("New session from checked items")
        start.setEnabled(self.can_start and bool(self.checked_items()))
        start.setToolTip("Starts a session from the ticked rows, including rows hidden by a filter.")
        start.triggered.connect(lambda _checked=False: self.new_from_checked.emit())
        menu.addSeparator()
        view_summary = menu.addAction("View summary")
        view_prompt = menu.addAction("View prompt")
        summary_row = item is not None and item.kind == "summary"
        prompt_row = item is not None and item.kind == "prompt"
        view_summary.setEnabled(summary_row)
        view_prompt.setEnabled(prompt_row)
        view_summary.setToolTip(
            "Open this summary." if summary_row else "Right-click a summary row."
        )
        view_prompt.setToolTip(
            "Open this prompt." if prompt_row else "Right-click a prompt row."
        )
        if summary_row:
            view_summary.triggered.connect(lambda _checked=False: self.view_item.emit(item))
        if prompt_row:
            view_prompt.triggered.connect(lambda _checked=False: self.view_item.emit(item))
        return menu

    def checked_items(self) -> list[HistoryItem]:
        """Rows whose checkbox is ticked, even if a kind filter is hiding them."""
        return [item for item in self._items if item.key in self._checked]

    def selected_items(self) -> list[HistoryItem]:
        chosen = []
        for item in self.checked_items():
            if self._boxes[item.kind].isChecked():
                chosen.append(item)
        return chosen

    def _rebuild(self) -> None:
        self.table.blockSignals(True)
        shown = [item for item in self._items if self._boxes[item.kind].isChecked()]
        self.table.setRowCount(len(shown))
        for row, item in enumerate(shown):
            check = QTableWidgetItem()
            check.setFlags(check.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(
                Qt.CheckState.Checked if item.key in self._checked else Qt.CheckState.Unchecked
            )
            check.setData(Qt.ItemDataRole.UserRole, item.key)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(item.kind))
            self.table.setItem(row, 2, QTableWidgetItem(item.created_at[11:16]))
            preview = " ".join(strip_grok_status(item.content).split())
            if len(preview) > 140:
                preview = preview[:137] + "..."
            self.table.setItem(row, 3, QTableWidgetItem(preview))
        self.table.blockSignals(False)
        if not self._quiet_rebuild:
            self.rebuilt.emit()

    def _on_cell_clicked(self, row: int, column: int) -> None:
        # The checkbox column only ticks the row. The rest of the row opens it.
        if column == 0:
            return
        self._activate_row(row)

    def _on_current_cell(self, row: int, _column: int, previous: int, _previous_column: int) -> None:
        if row < 0 or row == previous:
            return
        self._activate_row(row)

    def _activate_row(self, row: int) -> None:
        item = self.item_for_row(row)
        if item is not None:
            self.item_activated.emit(item)

    def _on_context_menu(self, pos) -> None:
        self.build_context_menu(self.item_at(pos)).exec(self.table.viewport().mapToGlobal(pos))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if not key:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(str(key))
        else:
            self._checked.discard(str(key))

    def _select_shown(self) -> None:
        for item in self._items:
            if self._boxes[item.kind].isChecked():
                self._checked.add(item.key)
        self._rebuild()

    def _clear(self) -> None:
        self._checked.clear()
        self._rebuild()

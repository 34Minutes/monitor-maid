"""Modal dialogs for the orchestrator. Results are delivered after the dialog closes."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox, QWidget

from monitor_maid.models import ApprovalRequest, ForwardRequest
from monitor_maid.ui.dialogs import ApprovalDialog, ForwardDialog


class WindowHooks:
    def __init__(self, parent_getter: Callable[[], QWidget]):
        self._parent = parent_getter

    def open_approval(self, request: ApprovalRequest, on_done) -> None:
        dialog = ApprovalDialog(request, self._parent())
        dialog.exec()
        result = dialog.outcome
        QTimer.singleShot(0, lambda: on_done(result))

    def open_forward(self, request: ForwardRequest, on_done) -> None:
        dialog = ForwardDialog(request, self._parent())
        dialog.exec()
        accepted = dialog.accepted_forward
        QTimer.singleShot(0, lambda: on_done(accepted))

    def show_blocked(self, request: ApprovalRequest, on_done) -> None:
        QMessageBox.warning(
            self._parent(),
            "Command blocked",
            "This command was not run.\n\n" + "\n".join(request.warnings or [request.reason]),
        )
        QTimer.singleShot(0, on_done)

"""Serial session controller.

Worker threads only perform network and process work. They never touch
SQLite or widgets. Results are applied on the GUI thread, one job at a time,
so a fast second message cannot land in the middle of an earlier reply.

Grok text is stored with visible_to_local=0. Command results that the local
model is allowed to see never include Grok's review.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from monitor_maid.config import Settings, effective_api_key
from monitor_maid.context import (
    build_grok_input,
    build_local_messages,
    build_review_input,
    compose_selection_prompt,
    grok_prompt_messages,
    normalise_prompt,
    normalise_summary,
    prompt_messages,
    seed_draft,
    shutdown_plan,
    summary_messages,
    verification_prompt,
)
from monitor_maid.db import Database, utc_now
from monitor_maid.executor import ExecResult, execute
from monitor_maid.instructions import parse_instructions, parse_prompts, parse_relays, scrub_relay
from monitor_maid.models import (
    NO_SUBSTANTIAL,
    ApprovalRequest,
    ApprovalResult,
    CommandRecord,
    ForwardRequest,
    HistoryItem,
    Instruction,
    Job,
    LLMReply,
    Session,
)
from monitor_maid.ollama_client import OllamaClient, OllamaError
from monitor_maid.policy import (
    apply_review_to_plan,
    assess_instruction,
    compile_denylist,
    escapes_cwd,
    parse_verdict,
    plan_approval,
    plan_relay,
)
from monitor_maid.xai_client import XAIClient

log = logging.getLogger("monitor_maid.orchestrator")


class _Bridge(QObject):
    finished = pyqtSignal(int, object, object)
    health = pyqtSignal(object, object)


class Orchestrator(QObject):
    transcript_changed = pyqtSignal()
    sessions_changed = pyqtSignal()
    session_changed = pyqtSignal()
    commands_changed = pyqtSignal()
    status_message = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    queue_depth = pyqtSignal(int)
    pending_changed = pyqtSignal(list)
    ollama_status = pyqtSignal(str)
    shutdown_active = pyqtSignal(bool)
    shutdown_progress = pyqtSignal(str)
    accepting_changed = pyqtSignal(bool)

    def __init__(self, db: Database, hooks, *, cwd: str, process_log_dir):
        super().__init__()
        self.db = db
        self.hooks = hooks
        self.cwd = cwd
        self.process_log_dir = process_log_dir
        self.session_id: str | None = None
        self.accepting = True
        self.queue: list[Job] = []
        self.busy = False
        self._job: Job | None = None
        self._bridge = _Bridge()
        self._bridge.finished.connect(self._on_worker)
        self._bridge.health.connect(self._on_health)
        self._ticket = 0
        self._worker_live = False
        self._background = True
        self._cb_ok: Callable | None = None
        self._cb_err: Callable | None = None
        self._grok_gen = 0
        self._retry_kinds: set[str] = set()
        self._retry_session: str | None = None
        self._instrs: list[Instruction] = []
        self._instr_i = 0
        self._instr_session: str | None = None
        self._instr_turn_id: str | None = None
        self._after_instr: Callable[[], None] | None = None
        self._relays = []
        self._relay_i = 0
        self._relay_session: str | None = None
        self._after_relays: Callable[[], None] | None = None
        self._review_text: str | None = None

    def current(self) -> Session | None:
        if not self.session_id:
            return None
        return self.db.get_session(self.session_id)

    def new_session(self, ollama_model: str | None = None) -> Session:
        settings = Settings.load(self.db)
        model = (ollama_model or "").strip() or settings.ollama_model
        if (ollama_model or "").strip() and settings.ollama_model != model:
            settings.ollama_model = model
            settings.save(self.db)
        session = self.db.create_session(
            title="New session",
            execution_mode=settings.default_execution_mode,
            auto_limit=settings.default_auto_limit,
            grok_relay_mode=settings.default_grok_relay_mode,
            grok_relay_limit=settings.default_grok_relay_limit,
            monitor_mode=settings.monitor_default,
            ollama_model=model,
        )
        self.session_id = session.id
        self.db.set_last_session(session.id)
        self._emit_session()
        log.info("opened session %s", session.id)
        return session

    def select_session(self, session_id: str) -> None:
        if self.busy:
            self.status_message.emit("Wait for the current reply before switching sessions.")
            return
        self.session_id = session_id
        self.db.set_last_session(session_id)
        self._retry_kinds.clear()
        self._emit_session()

    def reopen_current(self) -> None:
        session = self.current()
        if session is None or session.status == "open":
            return
        self.db.reopen_session(session.id)
        self._emit_session()

    def set_execution_mode(self, mode: str, limit: int | None = None) -> None:
        session = self.current()
        if session is None:
            return
        fields: dict = {"execution_mode": mode}
        if limit is not None:
            fields["auto_limit"] = max(0, int(limit))
        self.db.update_session(session.id, **fields)
        settings = Settings.load(self.db)
        settings.default_execution_mode = mode
        if limit is not None:
            settings.default_auto_limit = max(0, int(limit))
        settings.save(self.db)
        self.session_changed.emit()

    def set_relay_mode(self, mode: str, limit: int | None = None) -> None:
        session = self.current()
        if session is None:
            return
        fields: dict = {"grok_relay_mode": mode}
        if limit is not None:
            fields["grok_relay_limit"] = max(0, int(limit))
        self.db.update_session(session.id, **fields)
        settings = Settings.load(self.db)
        settings.default_grok_relay_mode = mode
        if limit is not None:
            settings.default_grok_relay_limit = max(0, int(limit))
        settings.save(self.db)
        self.session_changed.emit()

    def set_monitor_mode(self, enabled: bool) -> None:
        session = self.current()
        if session is None:
            return
        self.db.update_session(session.id, monitor_mode=bool(enabled))
        settings = Settings.load(self.db)
        settings.monitor_default = bool(enabled)
        settings.save(self.db)
        self.session_changed.emit()

    def set_ollama_model(self, model: str) -> None:
        """Use this model for the open session, and as the default after it."""
        session = self.current()
        cleaned = model.strip()
        if session is None or session.status != "open" or not cleaned:
            return
        changed = session.ollama_model != cleaned
        if changed:
            self.db.update_session(session.id, ollama_model=cleaned)
        settings = Settings.load(self.db)
        if settings.ollama_model != cleaned:
            settings.ollama_model = cleaned
            settings.save(self.db)
        if changed:
            self.session_changed.emit()

    def submit_local(self, text: str) -> bool:
        cleaned = text.strip()
        if not cleaned or not self._can_submit():
            return False
        self._enqueue(Job(kind="local", local_text=cleaned))
        return True

    def submit_grok(self, text: str) -> bool:
        cleaned = text.strip()
        if not cleaned or not self._can_submit():
            return False
        self._enqueue(Job(kind="grok", grok_text=cleaned, allow_relay=True))
        return True

    def submit_both(self, local_text: str, grok_text: str) -> bool:
        local_clean = local_text.strip()
        grok_clean = grok_text.strip()
        if not local_clean and not grok_clean:
            return False
        if not self._can_submit():
            return False
        if local_clean and grok_clean:
            self._enqueue(
                Job(kind="both", local_text=local_clean, grok_text=grok_clean, allow_relay=True)
            )
        elif local_clean:
            self._enqueue(Job(kind="local", local_text=local_clean))
        else:
            self._enqueue(Job(kind="grok", grok_text=grok_clean, allow_relay=True))
        return True

    def submit_selection_local(self, items: list[HistoryItem], question: str) -> bool:
        if not items or not self._can_submit():
            return False
        self._enqueue(
            Job(kind="local", local_text=compose_selection_prompt(items, question, for_local=True))
        )
        return True

    def submit_selection_grok(self, items: list[HistoryItem], question: str) -> bool:
        if not items or not self._can_submit():
            return False
        self._enqueue(
            Job(
                kind="grok",
                grok_text=compose_selection_prompt(items, question),
                allow_relay=True,
            )
        )
        return True

    def submit_verification(self, items: list[HistoryItem], question: str) -> bool:
        if not items or not self._can_submit():
            return False
        self._enqueue(
            Job(
                kind="verify",
                grok_text=verification_prompt(items, question),
                allow_relay=False,
                reply_kind="verification",
            )
        )
        return True

    def retry_last(self) -> bool:
        if not self._retry_kinds or not self._can_submit():
            return False
        if self._retry_session != self.session_id:
            return False
        if "local" in self._retry_kinds and "grok" in self._retry_kinds:
            kind = "both"
        elif "local" in self._retry_kinds:
            kind = "local"
        else:
            kind = "grok"
        self._retry_kinds.clear()
        self._enqueue(Job(kind=f"{kind}_retry", session_id=self._retry_session))
        return True

    def request_summary(self) -> bool:
        """Ask the local model for a summary and store it. The session stays open."""
        return self._request_note("summary")

    def request_prompt(self, author: str = "local_llm") -> bool:
        """Ask the local model or Grok for a prompt. Store one only if the reply marks it."""
        if author == "grok":
            return self._request_note("prompt_grok")
        return self._request_note("prompt")

    def selection_draft(self, items: list[HistoryItem]) -> str:
        """Reload the chosen rows and return the text for the input box.

        Nothing is written to the session. The user edits this text and sends
        it when they are ready.
        """
        return seed_draft(self._reload_items(items))

    def rename_session(self, title: str) -> bool:
        """Keep a name the user chose. Later messages do not replace it."""
        session = self.current()
        if session is None:
            self.status_message.emit("Open a session first.")
            return False
        cleaned = " ".join(title.split())
        if not cleaned:
            self.status_message.emit("Type a name for the session.")
            return False
        if len(cleaned) > 120:
            cleaned = cleaned[:117] + "..."
        if cleaned != session.title:
            self.db.update_session(session.id, title=cleaned)
            self._emit_session()
        self.status_message.emit("Session renamed.")
        return True

    def request_end(
        self,
        on_done: Callable[[], None],
        session_id: str | None = None,
        *,
        store_notes: bool = True,
    ) -> None:
        self.accepting = False
        self.accepting_changed.emit(False)
        sid = session_id or self.session_id
        if sid is None:
            QTimer.singleShot(0, on_done)
            return
        if store_notes:
            self.status_message.emit("Ending the session after queued messages finish.")
        else:
            self.status_message.emit("Closing the session without a summary.")
        self._enqueue(
            Job(kind="end", session_id=sid, on_done=on_done, store_notes=store_notes)
        )

    def cancel_shutdown(self) -> None:
        job = self._job
        if job is None or job.kind != "end":
            return
        self._invalidate_worker()
        if job.session_id:
            self._store_shutdown(job.session_id, "Summary skipped.")
        self._finish_job()

    def abandon(self, session_id: str) -> None:
        note = "Session was interrupted and closed without a summary."
        self.db.add_summary(session_id, note)
        self.db.add_turn(
            session_id,
            actor="system",
            kind="summary",
            content=note,
            visible_to_local=False,
        )
        self.db.mark_closed(session_id)
        self._emit_session()

    def mark_clean_exit(self) -> None:
        self._background = False
        self.db.set_running(False, self.session_id)

    def check_ollama(self) -> None:
        settings = Settings.load(self.db)
        client = OllamaClient(settings.ollama_url)

        def work() -> None:
            try:
                models = client.list_models()
                error = None
            except Exception as exc:  # noqa: BLE001 — surface any probe failure in the status bar
                models = None
                error = exc
            _emit_bridge(self, "health", models, error)

        threading.Thread(target=work, name="monitor-maid-health", daemon=True).start()

    # --- queue -------------------------------------------------------------

    def _can_submit(self) -> bool:
        if not self.accepting:
            self.status_message.emit("The session is ending. This message was not queued.")
            return False
        session = self.current()
        if session is None:
            return False
        if session.status != "open":
            self.status_message.emit("This session is closed. Reopen it to continue.")
            return False
        return True

    def _enqueue(self, job: Job) -> None:
        if job.session_id is None:
            job.session_id = self.session_id
        self.queue.append(job)
        self._emit_queue()
        self._pump()

    def _emit_queue(self) -> None:
        pending = []
        for job in self.queue:
            text = job.local_text or job.grok_text or job.kind
            pending.append(" ".join(str(text).split())[:80])
        self.queue_depth.emit(len(self.queue) + (1 if self.busy else 0))
        self.pending_changed.emit(pending)

    def _emit_session(self) -> None:
        self.sessions_changed.emit()
        self.session_changed.emit()
        self.transcript_changed.emit()
        self.commands_changed.emit()

    def _pump(self) -> None:
        if self.busy or not self.queue:
            return
        self.busy = True
        self.busy_changed.emit(True)
        self._job = self.queue.pop(0)
        self._emit_queue()
        job = self._job
        try:
            if job.kind == "end":
                self._start_end()
            elif job.kind == "local_retry":
                self._start_local(job.session_id or "", self._finish_job)
            elif job.kind == "grok_retry":
                self._start_grok(
                    job.session_id or "",
                    self._finish_job,
                    allow_relay=True,
                    reply_kind="message",
                )
            elif job.kind == "both_retry":
                self._start_local(job.session_id or "", self._then_grok)
            elif job.kind == "verify":
                self._prepare_turns(job)
                self._start_grok(
                    job.session_id or "",
                    self._finish_job,
                    allow_relay=False,
                    reply_kind="verification",
                )
            elif job.kind == "grok":
                self._prepare_turns(job)
                self._start_grok(
                    job.session_id or "",
                    self._finish_job,
                    allow_relay=job.allow_relay,
                    reply_kind=job.reply_kind,
                )
            elif job.kind == "local":
                self._prepare_turns(job)
                self._start_local(job.session_id or "", self._finish_job)
            elif job.kind == "both":
                self._prepare_turns(job)
                self._start_local(job.session_id or "", self._then_grok)
            elif job.kind == "make_summary":
                self._start_manual_summary(job.session_id or "")
            elif job.kind == "make_prompt":
                self._start_manual_prompt(job.session_id or "")
            elif job.kind == "make_prompt_grok":
                self._start_grok_prompt(job.session_id or "")
            else:
                log.error("unknown job kind %s", job.kind)
                self._finish_job()
        except Exception:
            log.exception("failed to start job %s", job.kind)
            self._notice(job.session_id, "The app failed while starting that message.")
            self._finish_job()

    def _prepare_turns(self, job: Job) -> None:
        sid = job.session_id
        if not sid:
            return
        if job.local_text:
            self.db.add_turn(
                sid,
                actor="user",
                kind="message",
                content=job.local_text,
                visible_to_local=True,
                metadata={"channel": "local"},
            )
        if job.grok_text:
            self.db.add_turn(
                sid,
                actor="user",
                kind="message",
                content=job.grok_text,
                visible_to_local=False,
                metadata={"channel": "grok"},
            )
        self.transcript_changed.emit()
        self.sessions_changed.emit()

    def _finish_job(self) -> None:
        job = self._job
        self._job = None
        self._instrs = []
        self._after_instr = None
        self._relays = []
        self._after_relays = None
        self._grok_gen += 1
        self.busy = False
        self.busy_changed.emit(False)
        self.shutdown_active.emit(False)
        self._emit_queue()
        callback = job.on_done if job else None
        if self.queue:
            self._pump()
        if callback:
            callback()

    def _then_grok(self) -> None:
        sid = self._job.session_id if self._job else None
        if not sid:
            self._finish_job()
            return
        self._start_grok(sid, self._finish_job, allow_relay=True, reply_kind="message")

    # --- model calls -------------------------------------------------------

    def _start_local(self, sid: str, on_complete: Callable[[], None]) -> None:
        settings = Settings.load(self.db)
        session = self.db.get_session(sid)
        model = (session.ollama_model if session and session.ollama_model else settings.ollama_model)
        messages = build_local_messages(self.db.list_turns(sid), cwd=self.cwd)
        client = OllamaClient(settings.ollama_url)
        timeout = settings.ollama_timeout
        self.status_message.emit(f"Waiting for the local model ({model})…")

        def work() -> LLMReply:
            return client.chat(model, messages, timeout)

        def ok(reply: LLMReply) -> None:
            self._retry_kinds.discard("local")
            turn = self._add_model_reply(sid, reply.text, "local_llm")
            self._begin_instructions(sid, turn.id, reply.text, on_complete)

        def err(exc: Exception) -> None:
            self._retry_kinds.add("local")
            self._retry_session = sid
            self._notice(sid, _ollama_message(exc, settings.ollama_url, model))
            on_complete()

        self._run(work, ok, err)

    def _start_grok(
        self,
        sid: str,
        on_complete: Callable[[], None],
        *,
        allow_relay: bool,
        reply_kind: str,
        attempt: int = 0,
        gen: int | None = None,
    ) -> None:
        if gen is None:
            self._grok_gen += 1
            gen = self._grok_gen
        if gen != self._grok_gen:
            return
        settings = Settings.load(self.db)
        key = effective_api_key(settings)
        messages = build_grok_input(
            self.db.list_turns(sid),
            budget=settings.context_budget,
        )
        client = XAIClient(settings.xai_base_url, key)
        timeout = settings.xai_timeout
        model = settings.xai_model
        self.status_message.emit(f"Waiting for Grok ({model})…")

        def work() -> LLMReply:
            return client.complete(model, messages, timeout)

        def ok(reply: LLMReply) -> None:
            if gen != self._grok_gen:
                return
            self._retry_kinds.discard("grok")
            if reply.refused:
                self.db.add_turn(
                    sid,
                    actor="grok",
                    kind="refusal",
                    content=reply.text,
                    visible_to_local=False,
                )
                self.transcript_changed.emit()
            else:
                self._add_model_reply(sid, reply.text, "grok", unmarked_kind=reply_kind)
            if reply.refused:
                self.status_message.emit("Grok refused the request. Nothing was forwarded.")
                on_complete()
                return
            if not allow_relay:
                on_complete()
                return
            blocks = [block for block in parse_relays(reply.text) if scrub_relay(block.text)]
            if not blocks:
                on_complete()
                return
            self._relays = blocks
            self._relay_i = 0
            self._relay_session = sid
            self._after_relays = on_complete
            self._next_relay()

        def err(exc: Exception) -> None:
            if gen != self._grok_gen:
                return
            kind = getattr(exc, "kind", "")
            if kind in {"rate_limit", "network"} and attempt < 2:
                delay = getattr(exc, "retry_after", None) or float(2 ** (attempt + 1))
                delay = min(max(delay, 1.0), 30.0)
                self.status_message.emit(
                    f"Grok is unavailable ({kind.replace('_', ' ')}). Retrying in {int(delay)}s."
                )
                QTimer.singleShot(
                    int(delay * 1000),
                    lambda: self._start_grok(
                        sid,
                        on_complete,
                        allow_relay=allow_relay,
                        reply_kind=reply_kind,
                        attempt=attempt + 1,
                        gen=gen,
                    ),
                )
                return
            self._retry_kinds.add("grok")
            self._retry_session = sid
            self._notice(sid, f"Grok request failed: {exc}")
            on_complete()

        self._run(work, ok, err)

    def _begin_instructions(
        self,
        sid: str,
        turn_id: str,
        text: str,
        on_complete: Callable[[], None],
    ) -> None:
        found = parse_instructions(text)
        self._instrs = found
        self._instr_i = 0
        self._instr_session = sid
        self._instr_turn_id = turn_id
        self._after_instr = on_complete
        if not found:
            on_complete()
            return
        self._next_instruction()

    def _next_instruction(self) -> None:
        if self._instr_i >= len(self._instrs):
            callback = self._after_instr or self._finish_job
            self._after_instr = None
            callback()
            return
        instruction = self._instrs[self._instr_i]
        self._instr_i += 1
        self._consider(instruction)

    def _consider(self, instruction: Instruction) -> None:
        sid = self._instr_session or ""
        session = self.db.get_session(sid)
        settings = Settings.load(self.db)
        patterns, errors = compile_denylist(settings.denylist)
        for error in errors:
            log.warning("skipping denylist entry: %s", error)
        outside = escapes_cwd(instruction.path, self.cwd) if instruction.path else False
        safety = assess_instruction(instruction, patterns, outside_cwd=outside)
        mode = session.execution_mode if session else "manual"
        used = session.auto_used if session else 0
        limit = session.auto_limit if session else 0
        plan = plan_approval(mode, used, limit, safety)
        self._review_text = None
        if plan == "block":
            self._finish_decision(instruction, "blocked", "blocked", " ".join(safety.reasons), None)
            request = self._approval_request(
                instruction, safety.reasons, blocked=True, review=None, review_error=None, reason="Blocked"
            )
            self.hooks.show_blocked(request, self._next_instruction)
            return
        wants_review = settings.grok_review_before_exec
        if wants_review and effective_api_key(settings):
            self._review_command(
                instruction,
                lambda text, error: self._after_prefetch(plan, instruction, text, error),
            )
            return
        if wants_review and plan == "auto":
            plan = "manual"
            safety.reasons = list(safety.reasons) + [
                "Grok review is required before automatic execution, and no API key is set."
            ]
        self._dispatch(plan, instruction, None, None, safety.reasons)

    def _after_prefetch(self, plan: str, instruction: Instruction, text: str | None, error: Exception | None) -> None:
        verdict = parse_verdict(text)
        failed = error is not None
        plan = apply_review_to_plan(plan, verdict, review_required=True, review_failed=failed)
        warnings = []
        if failed:
            warnings.append(f"Grok review failed: {error}")
        elif text:
            warnings.append(f"Grok review ({verdict}).")
        self._dispatch(plan, instruction, text, str(error) if error else None, warnings)

    def _dispatch(
        self,
        plan: str,
        instruction: Instruction,
        review_text: str | None,
        review_error: str | None,
        warnings: list[str],
    ) -> None:
        self._review_text = review_text
        if plan == "auto":
            self._execute(instruction, "auto", review_text)
            return
        reason = "Needs approval."
        if plan == "manual" and warnings:
            reason = warnings[0]
        request = self._approval_request(
            instruction,
            warnings,
            blocked=False,
            review=review_text,
            review_error=review_error,
            reason=reason,
        )
        self.hooks.open_approval(
            request,
            lambda result: self._on_approval(instruction, result, review_text),
        )

    def _on_approval(
        self,
        instruction: Instruction,
        result: ApprovalResult,
        review_text: str | None,
    ) -> None:
        if result.decision == "review":
            edited = self._edited(instruction, result)
            self._review_command(
                edited,
                lambda text, error: self._dispatch(
                    "manual",
                    edited,
                    text,
                    str(error) if error else None,
                    [f"Grok review failed: {error}"] if error else ["Review attached."],
                ),
            )
            return
        if result.decision != "approve":
            self._finish_decision(instruction, "rejected", "rejected", "Rejected by the user.", review_text)
            self._next_instruction()
            return
        edited = self._edited(instruction, result)
        settings = Settings.load(self.db)
        patterns, _errors = compile_denylist(settings.denylist)
        outside = escapes_cwd(edited.path, self.cwd) if edited.path else False
        safety = assess_instruction(edited, patterns, outside_cwd=outside)
        if safety.level == "block":
            self._finish_decision(edited, "blocked", "blocked", " ".join(safety.reasons), review_text)
            self._next_instruction()
            return
        self._execute(edited, "approved", review_text)

    def _execute(self, instruction: Instruction, decision: str, review_text: str | None) -> None:
        sid = self._instr_session or ""
        settings = Settings.load(self.db)
        patterns, _errors = compile_denylist(settings.denylist)
        if decision == "auto":
            session = self.db.get_session(sid)
            if session is not None:
                self.db.update_session(sid, auto_used=session.auto_used + 1)
                self.session_changed.emit()
        allowed = self.db.allowed_pids()
        timeout = settings.command_timeout
        log_dir = self.process_log_dir
        cwd = self.cwd
        mode = "auto" if decision == "auto" else "approved"
        self.status_message.emit(f"Running {instruction.op} ({decision})…")

        def work() -> ExecResult:
            return execute(
                instruction,
                decision=mode,
                timeout=timeout,
                cwd_default=cwd,
                denylist=patterns,
                allowed_pids=allowed,
                log_dir=log_dir,
            )

        def ok(result: ExecResult) -> None:
            if result.refused:
                self._finish_decision(
                    instruction,
                    "blocked",
                    "blocked",
                    result.error or "Refused.",
                    review_text,
                    result,
                )
            else:
                status = "timeout" if result.timed_out else ("done" if result.ok else "failed")
                self._finish_decision(instruction, status, decision, result.error or "", review_text, result)
                if instruction.op == "start_process" and result.pid:
                    self.db.add_process(result.pid, sid, instruction.command or "", result.log_path or "")
                if instruction.op == "stop_process" and result.ok and instruction.pid:
                    self.db.mark_process_stopped(instruction.pid)
            self._next_instruction()

        def err(exc: Exception) -> None:
            self._finish_decision(instruction, "failed", decision, str(exc), review_text)
            self._next_instruction()

        self._run(work, ok, err)

    def _finish_decision(
        self,
        instruction: Instruction,
        status: str,
        decision: str,
        reason: str,
        review_text: str | None,
        result: ExecResult | None = None,
    ) -> None:
        sid = self._instr_session or ""
        now = utc_now()
        record = CommandRecord(
            id=str(uuid.uuid4()),
            session_id=sid,
            turn_id=self._instr_turn_id,
            proposed_at=now,
            command=instruction.shell_text(),
            cwd=instruction.cwd or self.cwd,
            kind=instruction.op,
            status=status,
            decision=decision,
            decision_reason=reason or None,
            grok_review=review_text,
            exit_code=None if result is None else result.exit_code,
            stdout=None if result is None else result.stdout,
            stderr=None if result is None else result.stderr,
            started_at=now if result is not None else None,
            finished_at=now if result is not None else None,
            timeout_s=None if result is None else Settings.load(self.db).command_timeout,
        )
        self.db.add_command(record)
        self.commands_changed.emit()
        lines = [f"Command {decision}.", instruction.shell_text()]
        if reason:
            lines.append(reason)
        if result is not None:
            if result.timed_out:
                lines.append(result.error or "Timed out.")
            if result.exit_code is not None:
                lines.append(f"Exit code: {result.exit_code}")
            if result.stdout:
                lines.append("stdout:\n" + result.stdout)
            if result.stderr:
                lines.append("stderr:\n" + result.stderr)
        self.db.add_turn(
            sid,
            actor="system",
            kind="command_result",
            content="\n".join(lines),
            visible_to_local=True,
        )
        self.transcript_changed.emit()
        log.info("command %s %s %s", decision, status, instruction.op)

    def _review_command(self, instruction: Instruction, on_done) -> None:
        sid = self._instr_session or ""
        settings = Settings.load(self.db)
        key = effective_api_key(settings)
        messages = build_review_input(
            self.db.list_turns(sid),
            instruction.shell_text(),
            budget=settings.context_budget,
        )
        client = XAIClient(settings.xai_base_url, key)
        model = settings.xai_model
        timeout = settings.xai_timeout
        self.status_message.emit("Asking Grok to review the command…")

        def work() -> LLMReply:
            return client.complete(model, messages, timeout)

        def ok(reply: LLMReply) -> None:
            self.db.add_turn(
                sid,
                actor="grok",
                kind="refusal" if reply.refused else "review",
                content=reply.text,
                visible_to_local=False,
            )
            self.transcript_changed.emit()
            on_done(reply.text, None)

        def err(exc: Exception) -> None:
            on_done(None, exc)

        self._run(work, ok, err)

    def _approval_request(
        self,
        instruction: Instruction,
        warnings: list[str],
        *,
        blocked: bool,
        review: str | None,
        review_error: str | None,
        reason: str,
    ) -> ApprovalRequest:
        settings = Settings.load(self.db)
        return ApprovalRequest(
            op=instruction.op,
            command=instruction.command or instruction.shell_text(),
            cwd=instruction.cwd or self.cwd,
            path=instruction.path,
            file_content=instruction.content,
            warnings=warnings,
            blocked=blocked,
            review_text=review,
            review_error=review_error,
            allow_review=bool(effective_api_key(settings)) and not blocked,
            reason=reason,
        )

    def _edited(self, instruction: Instruction, result: ApprovalResult) -> Instruction:
        if instruction.op in {"shell", "start_process"}:
            return Instruction(
                op=instruction.op,
                raw=result.command or "",
                command=result.command or "",
                cwd=result.cwd or instruction.cwd,
            )
        if instruction.op == "write_file":
            return Instruction(
                op="write_file",
                raw=instruction.raw,
                path=result.path or instruction.path,
                content=result.file_content if result.file_content is not None else (instruction.content or ""),
            )
        if instruction.op == "read_file":
            return Instruction(
                op="read_file",
                raw=instruction.raw,
                path=result.path or instruction.path,
            )
        return instruction

    # --- relays ------------------------------------------------------------

    def _next_relay(self) -> None:
        if self._relay_i >= len(self._relays):
            callback = self._after_relays or self._finish_job
            self._relays = []
            self._after_relays = None
            callback()
            return
        block = self._relays[self._relay_i]
        self._relay_i += 1
        text = scrub_relay(block.text)
        sid = self._relay_session or ""
        if not text:
            self._next_relay()
            return
        session = self.db.get_session(sid)
        if session is None:
            self._next_relay()
            return
        decision = plan_relay(
            session.grok_relay_mode,
            session.grok_relay_used,
            session.grok_relay_limit,
            partial=block.partial,
        )
        if decision == "auto":
            self.db.update_session(sid, grok_relay_used=session.grok_relay_used + 1)
            self.session_changed.emit()
            self._inject_relay(sid, text)
            return
        warnings = []
        if block.partial:
            warnings.append("This suggestion was not closed with a fence. It will not be sent automatically.")
        self.hooks.open_forward(
            ForwardRequest(text=text, partial=block.partial, warnings=warnings),
            lambda accepted, sid=sid, text=text: self._on_forward(sid, text, accepted),
        )

    def _on_forward(self, sid: str, text: str, accepted: bool) -> None:
        if not accepted:
            self._notice(sid, "A suggested follow-up was not sent to the local model.")
            self._next_relay()
            return
        self._inject_relay(sid, text)

    def _inject_relay(self, sid: str, text: str) -> None:
        self.db.add_turn(
            sid,
            actor="user",
            kind="relay",
            content=text,
            visible_to_local=True,
            metadata={"channel": "local", "relay": True},
        )
        self.transcript_changed.emit()
        self._start_local(sid, self._next_relay)

    def _request_note(self, kind: str) -> bool:
        if not self.accepting:
            self.status_message.emit("The session is ending. Nothing was stored.")
            return False
        session = self.current()
        if session is None:
            self.status_message.emit("Open a session first.")
            return False
        if session.status == "closing":
            self.status_message.emit("This session is already closing.")
            return False
        if kind == "prompt_grok" and not effective_api_key(Settings.load(self.db)):
            self.status_message.emit("Add an xAI API key in Settings, or set XAI_API_KEY.")
            return False
        plan = shutdown_plan(self.db.list_turns(session.id))
        if kind == "summary":
            empty = "There is nothing in this session to summarise yet."
            job_kind = "make_summary"
            preview = "Create summary"
            label = "Summary"
        elif kind == "prompt_grok":
            empty = "There is nothing in this session to make a prompt from yet."
            job_kind = "make_prompt_grok"
            preview = "Create prompt with Grok"
            label = "Prompt"
        else:
            empty = "There is nothing in this session to make a prompt from yet."
            job_kind = "make_prompt"
            preview = "Create prompt"
            label = "Prompt"
        if plan == "skip":
            self.status_message.emit(empty)
            return False
        if plan == "note":
            if kind == "summary":
                self._store_manual_summary(session.id, NO_SUBSTANTIAL)
            else:
                self.status_message.emit("This session is too short to make a prompt from.")
            return True
        job = Job(kind=job_kind, session_id=session.id, local_text=preview)
        self._enqueue(job)
        if self._job is not job:
            self.status_message.emit(f"{label} queued. This session stays open.")
        return True

    def _reload_items(self, items: list[HistoryItem]) -> list[HistoryItem]:
        loaded: list[HistoryItem] = []
        for item in items:
            fresh = self._reload_item(item)
            if fresh is not None and fresh.content.strip():
                loaded.append(fresh)
        return loaded

    def _reload_item(self, item: HistoryItem) -> HistoryItem | None:
        if item.kind in {"question", "answer", "grok"}:
            turn = self.db.get_turn(item.source_id)
            if turn is None:
                return None
            return HistoryItem(
                key=item.key or f"turn:{turn.id}",
                kind=item.kind,
                actor=turn.actor,
                created_at=turn.created_at,
                content=turn.content,
                source_id=turn.id,
            )
        if item.kind == "summary":
            summary = self.db.get_summary(item.source_id)
            if summary is None:
                return None
            return HistoryItem(
                key=item.key or f"summary:{summary.id}",
                kind="summary",
                actor="local_llm",
                created_at=summary.created_at,
                content=summary.content,
                source_id=summary.id,
            )
        if item.kind in {"prompt", "conclusion"}:
            prompt = self.db.get_prompt(item.source_id)
            if prompt is None:
                return None
            return HistoryItem(
                key=item.key or f"prompt:{prompt.id}",
                kind="prompt",
                actor=prompt.author or "local_llm",
                created_at=prompt.created_at,
                content=prompt.content,
                source_id=prompt.id,
            )
        return None

    def _store_manual_summary(self, sid: str, summary: str) -> None:
        summary_text = normalise_summary(summary)
        self.db.add_summary(sid, summary_text)
        self.db.add_turn(
            sid,
            actor="local_llm",
            kind="summary",
            content=summary_text,
            visible_to_local=True,
        )
        self._emit_session()
        session = self.db.get_session(sid)
        if session is not None and session.status == "open":
            self.status_message.emit("Summary stored. This session stays open.")
        else:
            self.status_message.emit("Summary stored.")
        log.info("stored manual summary for %s", sid)

    def _add_model_reply(
        self,
        sid: str,
        text: str,
        author: str,
        *,
        unmarked_kind: str = "message",
    ):
        """Store a model reply. A prompt record is written only for a closed prompt fence."""
        bodies = parse_prompts(text)
        stored_author = "grok" if author == "grok" else "local_llm"
        turn = self.db.add_turn(
            sid,
            actor=stored_author,
            kind="prompt" if bodies else unmarked_kind,
            content=text,
            visible_to_local=stored_author != "grok",
        )
        for body in bodies:
            self.db.add_prompt(sid, normalise_prompt(body), author=stored_author)
        self.transcript_changed.emit()
        if bodies:
            self.status_message.emit(
                "Prompt stored." if len(bodies) == 1 else f"{len(bodies)} prompts stored."
            )
            log.info("stored %s prompt(s) for %s by %s", len(bodies), sid, stored_author)
        return turn

    def _start_manual_summary(self, sid: str) -> None:
        self.status_message.emit("Asking the local model for a summary…")
        session = self.db.get_session(sid)
        if session is None:
            self._finish_job()
            return
        settings = Settings.load(self.db)
        model = session.ollama_model if session.ollama_model else settings.ollama_model
        messages = summary_messages(self.db.list_turns(sid), cwd=self.cwd)
        client = OllamaClient(settings.ollama_url)
        timeout = settings.summary_timeout

        def work() -> LLMReply:
            return client.chat(model, messages, timeout)

        def ok(reply: LLMReply) -> None:
            if self.db.get_session(sid) is None:
                self._finish_job()
                return
            self._store_manual_summary(sid, normalise_summary(reply.text))
            self._finish_job()

        def err(exc: Exception) -> None:
            self.status_message.emit(f"Could not create a summary: {exc}")
            self._finish_job()

        self._run(work, ok, err)

    def _start_manual_prompt(self, sid: str) -> None:
        self.status_message.emit("Asking the local model for a prompt…")
        session = self.db.get_session(sid)
        if session is None:
            self._finish_job()
            return
        settings = Settings.load(self.db)
        model = session.ollama_model if session.ollama_model else settings.ollama_model
        messages = prompt_messages(self.db.list_turns(sid), cwd=self.cwd)
        client = OllamaClient(settings.ollama_url)
        timeout = settings.summary_timeout

        def work() -> LLMReply:
            return client.chat(model, messages, timeout)

        def ok(reply: LLMReply) -> None:
            if self.db.get_session(sid) is None:
                self._finish_job()
                return
            self._add_model_reply(sid, reply.text, "local_llm")
            if not parse_prompts(reply.text):
                self.status_message.emit(
                    "The reply was not marked as a prompt. No prompt was stored."
                )
            self._finish_job()

        def err(exc: Exception) -> None:
            self.status_message.emit(f"Could not create a prompt: {exc}")
            self._finish_job()

        self._run(work, ok, err)

    def _start_grok_prompt(self, sid: str) -> None:
        self.status_message.emit("Asking Grok for a prompt…")
        session = self.db.get_session(sid)
        if session is None:
            self._finish_job()
            return
        settings = Settings.load(self.db)
        key = effective_api_key(settings)
        if not key:
            self.status_message.emit("Add an xAI API key in Settings, or set XAI_API_KEY.")
            self._finish_job()
            return
        messages = grok_prompt_messages(
            self.db.list_turns(sid),
            budget=settings.context_budget,
        )
        client = XAIClient(settings.xai_base_url, key)
        timeout = settings.xai_timeout
        model = settings.xai_model

        def work() -> LLMReply:
            return client.complete(model, messages, timeout)

        def ok(reply: LLMReply) -> None:
            if self.db.get_session(sid) is None:
                self._finish_job()
                return
            if reply.refused:
                self.status_message.emit("Grok refused the request. Nothing was stored.")
                self._finish_job()
                return
            self._add_model_reply(sid, reply.text, "grok")
            if not parse_prompts(reply.text):
                self.status_message.emit(
                    "The reply was not marked as a prompt. No prompt was stored."
                )
            self._finish_job()

        def err(exc: Exception) -> None:
            self.status_message.emit(f"Could not create a prompt: {exc}")
            self._finish_job()

        self._run(work, ok, err)

    # --- shutdown ----------------------------------------------------------

    def _start_end(self) -> None:
        job = self._job
        sid = job.session_id if job else None
        store_notes = True if job is None else job.store_notes
        if store_notes:
            self.shutdown_active.emit(True)
            self.shutdown_progress.emit("Ending the session…")
        if not sid:
            self._finish_job()
            return
        session = self.db.get_session(sid)
        if session is None or session.status == "closed":
            self._finish_job()
            return
        if not store_notes:
            self._close_without_notes(sid)
            self._finish_job()
            return
        self.db.update_session(sid, status="closing")
        self.sessions_changed.emit()
        turns = self.db.list_turns(sid)
        plan = shutdown_plan(turns)
        log.info("shutdown %s for %s", plan, sid)
        if plan == "skip":
            self.db.delete_session_if_empty(sid)
            if self.session_id == sid:
                self.session_id = None
            self._emit_session()
            self._finish_job()
            return
        if plan == "note":
            self._store_shutdown(sid, NO_SUBSTANTIAL)
            self._finish_job()
            return
        self._ask_summary(sid)

    def _ask_summary(self, sid: str) -> None:
        self.shutdown_progress.emit("Asking the local model for a summary…")
        settings = Settings.load(self.db)
        session = self.db.get_session(sid)
        model = session.ollama_model if session and session.ollama_model else settings.ollama_model
        messages = summary_messages(self.db.list_turns(sid), cwd=self.cwd)
        client = OllamaClient(settings.ollama_url)
        timeout = settings.summary_timeout

        def work() -> LLMReply:
            return client.chat(model, messages, timeout)

        def ok(reply: LLMReply) -> None:
            self._store_shutdown(sid, normalise_summary(reply.text))
            self._finish_job()

        def err(exc: Exception) -> None:
            self._store_shutdown(sid, f"Summary unavailable: {exc}")
            self._finish_job()

        self._run(work, ok, err)

    def _close_without_notes(self, sid: str) -> None:
        """Close the session and leave summaries and prompts unchanged."""
        turns = self.db.list_turns(sid)
        if not turns:
            self.db.delete_session_if_empty(sid)
            if self.session_id == sid:
                self.session_id = None
            self._emit_session()
            log.info("discarded empty session %s", sid)
            return
        self.db.mark_closed(sid)
        self._emit_session()
        self.status_message.emit("Session closed without a summary.")
        log.info("closed session %s without a summary", sid)

    def _store_shutdown(self, sid: str, summary: str) -> None:
        summary_text = normalise_summary(summary)
        self.db.add_summary(sid, summary_text)
        self.db.add_turn(
            sid,
            actor="local_llm",
            kind="summary",
            content=summary_text,
            visible_to_local=True,
        )
        self.db.mark_closed(sid)
        self._emit_session()
        log.info("closed session %s", sid)

    # --- workers -----------------------------------------------------------

    def _run(self, fn: Callable[[], object], on_ok: Callable, on_err: Callable) -> None:
        if self._worker_live:
            raise RuntimeError("A background call is already running.")
        self._ticket += 1
        ticket = self._ticket
        self._worker_live = True
        self._cb_ok = on_ok
        self._cb_err = on_err

        def work() -> None:
            try:
                result = fn()
                error = None
            except Exception as exc:  # noqa: BLE001 — returned to the GUI thread
                log.exception("background call failed")
                result = None
                error = exc
            _emit_bridge(self, "finished", ticket, result, error)

        threading.Thread(target=work, name="monitor-maid-job", daemon=True).start()

    def _on_worker(self, ticket: int, result: object, error: object) -> None:
        if ticket != self._ticket:
            return
        self._worker_live = False
        on_ok = self._cb_ok
        on_err = self._cb_err
        self._cb_ok = None
        self._cb_err = None
        try:
            if error is not None:
                if on_err:
                    on_err(error)
            elif on_ok:
                on_ok(result)
        except Exception as exc:  # noqa: BLE001 — keep the queue moving
            log.exception("job callback failed")
            sid = self._job.session_id if self._job else self.session_id
            self._notice(sid, f"Unexpected error: {exc}")
            if self.busy:
                self._finish_job()

    def _invalidate_worker(self) -> None:
        self._ticket += 1
        self._worker_live = False
        self._cb_ok = None
        self._cb_err = None

    def _on_health(self, models: object, error: object) -> None:
        if error is not None:
            kind = getattr(error, "kind", "offline")
            self.ollama_status.emit(str(kind))
            return
        self.ollama_status.emit("online")
        if isinstance(models, list):
            self.status_message.emit(
                "Ollama online. Models: " + (", ".join(models) if models else "(none pulled)")
            )

    def _notice(self, sid: str | None, text: str) -> None:
        if not sid:
            self.status_message.emit(text)
            return
        self.db.add_turn(
            sid,
            actor="system",
            kind="error",
            content=text,
            visible_to_local=False,
        )
        self.transcript_changed.emit()
        self.status_message.emit(text.split("\n", 1)[0][:180])


def _emit_bridge(orchestrator: Orchestrator, name: str, *args) -> None:
    """Emit a worker signal unless the window is already gone.

    A health probe can outlive the window: Ollama's connect timeout is longer
    than an empty-session quit. Touching a deleted QObject raises RuntimeError,
    which pytest reports even after the test has passed.
    """
    if not orchestrator._background:
        return
    try:
        signal = getattr(orchestrator._bridge, name)
        signal.emit(*args)
    except RuntimeError:
        return


def _ollama_message(exc: Exception, url: str, model: str) -> str:
    kind = getattr(exc, "kind", "")
    if kind == "offline":
        return (
            f"Could not reach Ollama at {url}. "
            "Start it with `ollama serve`, then press Retry."
        )
    if kind == "model_missing":
        return (
            f"{exc}\nThis session is still set to {model}. "
            "Press Retry after saving an installed model in Settings."
        )
    if kind == "timeout":
        return f"Ollama timed out. {exc}\nPress Retry to try the same message again."
    if isinstance(exc, OllamaError):
        return f"Local model error: {exc}"
    return f"Local model error: {exc}"

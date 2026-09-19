"""Single-use native move ownership and bounded asynchronous correlation."""

from dataclasses import dataclass, field
import logging
import secrets
import threading
import time

from .window_match import NativeWindowObservation, match_window
from .windows_drag import reread_move_bounds


logger = logging.getLogger(__name__)


@dataclass
class CorrelationTask:
    gesture_id: str
    token: object
    source_display_id: str
    source_side: str
    topology_version: int
    expires_at: float
    cancelled: threading.Event = field(default_factory=threading.Event)
    stages: dict = field(default_factory=dict)
    request_id: str | None = None
    matched: object = None
    candidate: dict | None = None
    failure: str | None = None
    dispatch_started: bool = False


def _spawn(callback):
    threading.Thread(target=callback, name="browser-handoff-correlation", daemon=True).start()


class BrowserHandoffCoordinator:
    """The input callback claims once; its task owns that token until completion.

    No metadata or native rectangle I/O occurs on the input callback. Cancellation
    invalidates queued snapshots/publications, without ever unclaiming a move.
    Clock, wait, scheduling and native reads are injected for deterministic tests.
    """

    def __init__(self, *, desktop, move_tracker, to_physical, send_candidate,
                 now=time.monotonic, token_ttl_seconds=1.0, max_pending=8,
                 read_bounds=reread_move_bounds, spawn=_spawn,
                 wait=lambda event, seconds: event.wait(seconds)):
        self.desktop = desktop
        self.move_tracker = move_tracker
        self.to_physical = to_physical
        self.send_candidate = send_candidate
        self.now = now
        self.token_ttl_seconds = float(token_ttl_seconds)
        self.max_pending = int(max_pending)
        self.read_bounds = read_bounds
        self.spawn = spawn
        self.wait = wait
        self._tasks = {}
        self._pending = {}
        self._lock = threading.RLock()
        previous = getattr(desktop, "on_snapshot", None)

        def on_snapshot(instance_id, message):
            if previous is not None:
                try:
                    previous(instance_id, message)
                except Exception:
                    pass
            self.handle_snapshot(instance_id, message)

        desktop.on_snapshot = on_snapshot

    def _stage(self, task, stage, **details):
        task.stages[stage] = self.now()
        logger.info("browser_handoff stage=%s gesture=%s details=%s", stage, task.gesture_id[:8], details)

    def _alive(self, task):
        return not task.cancelled.is_set() and self.now() < task.expires_at

    def claim_edge(self, *, display_rect, edge_region, source_display_id, source_side, topology_version):
        logger.info("browser_handoff stage=edge_claim display=%s side=%s topology=%s", source_display_id, source_side, topology_version)
        if (type(topology_version) is not int or topology_version < 0
                or type(source_display_id) is not str or not source_display_id
                or source_side not in {"left", "right", "top", "bottom"}):
            return None
        with self._lock:
            for key, task in tuple(self._tasks.items()):
                if not task.dispatch_started and not self._alive(task):
                    task.cancelled.set()
                    self._tasks.pop(key, None)
                    self._pending.pop(task.request_id, None)
            if len(self._tasks) >= self.max_pending:
                logger.info("browser_handoff stage=pending_capacity_rejected")
                return None
            claim_now = self.now()
            token = self.move_tracker.claim_active_move(now=claim_now)
            completed = False
            if token is None:
                consume = getattr(self.move_tracker, "consume_eligible_move", None)
                token = consume(now=claim_now) if consume else None
                completed = token is not None
            if token is None:
                diagnostic = getattr(self.move_tracker, "diagnostic_snapshot", lambda: None)
                logger.info("browser_handoff stage=move_token_missing diagnostics=%s", diagnostic())
                return None
            task = CorrelationTask(secrets.token_hex(16), token, source_display_id,
                                   source_side, topology_version, claim_now + self.token_ttl_seconds)
            self._tasks[task.gesture_id] = task
            self._stage(task, "edge_claimed")
            if completed:
                self._stage(task, "completed_move_token_consumed")
        self.spawn(lambda: self._correlate(task))
        return task.gesture_id

    def _pause(self, task, seconds=0.01):
        self.wait(task.cancelled, max(0, min(seconds, task.expires_at - self.now())))

    def _correlate(self, task):
        outcome = "deadline_expired"
        refresh_id = None
        try:
            completion = task.token.completion
            while self._alive(task) and completion is not None and completion.ended_at is None:
                if completion.invalidated:
                    outcome = "move_invalidated"
                    return
                self._pause(task)
            if not self._alive(task):
                return
            if completion is not None and completion.invalidated:
                outcome = "move_invalidated"
                return
            ended_at = completion.ended_at if completion is not None else task.token.completed_at
            task.stages["move_end"] = ended_at
            self._stage(task, "move_end_observed", ended_at=ended_at)
            bounds = self.read_bounds(task.token)
            self._stage(task, "native_rect_read")
            native = NativeWindowObservation(task.token.hwnd, task.token.process_id,
                                             task.token.process_created, bounds, self.now())
            # One initial refresh plus at most two short-backoff revisions.
            for attempt in range(3):
                if not self._alive(task):
                    return
                refresh_id = secrets.token_hex(16)
                if not self.desktop.request_metadata_refresh(refresh_id):
                    outcome = "metadata_request_failed"
                    return
                self._stage(task, "metadata_requested", attempt=attempt)
                candidates = None
                while self._alive(task):
                    candidates = self.desktop.browser_candidates(self.to_physical, refresh_id=refresh_id)
                    if candidates is not None:
                        break
                    self._pause(task)
                if not self._alive(task):
                    return
                matched = match_window(native, candidates, now=self.now())
                native_rect = (bounds.left, bounds.top, bounds.right, bounds.bottom)
                for candidate in candidates:
                    rect = (candidate.bounds.left, candidate.bounds.top, candidate.bounds.right, candidate.bounds.bottom)
                    self._stage(task, "correlation_rect", instance=candidate.browser_instance_id,
                                window=candidate.window_id, bridge_epoch=candidate.bridge_epoch,
                                revision=candidate.metadata_revision, received_at=candidate.observed_at,
                                native_bounds=native_rect, browser_bounds=rect,
                                edge_delta=tuple(b - n for b, n in zip(rect, native_rect)))
                self.desktop.release_metadata_refresh(refresh_id)
                refresh_id = None
                if matched is not None:
                    self._stage(task, "window_matched", move_end_to_revision_ms=round((matched.observed_at - ended_at) * 1000, 3))
                    task.matched = matched
                    break
                self._stage(task, "window_match_rejected", candidates=len(candidates))
                if attempt < 2:
                    self._pause(task, 0.025 * (attempt + 1))
            if task.matched is None:
                outcome = "window_match_rejected"
                return
            with self._lock:
                if not self._alive(task):
                    return
                task.request_id = secrets.token_hex(16)
                self._pending[task.request_id] = task
            matched = task.matched
            if not self.desktop.bridge_is_current(matched.browser_instance_id, matched.bridge_epoch):
                outcome = "bridge_lost"
                return
            self._stage(task, "snapshot_requested", instance=matched.browser_instance_id, window=matched.window_id)
            if not self.desktop.request_snapshot(matched.browser_instance_id, matched.window_id, task.request_id,
                                                 expected_epoch=matched.bridge_epoch):
                outcome = "snapshot_request_failed"
                return
            while self._alive(task) and task.candidate is None and task.failure is None:
                if not self.desktop.bridge_is_current(matched.browser_instance_id, matched.bridge_epoch):
                    outcome = "bridge_lost"
                    return
                self._pause(task)
            with self._lock:
                if not self._alive(task):
                    return
                if task.failure:
                    outcome = task.failure
                    return
                if not self.desktop.bridge_is_current(matched.browser_instance_id, matched.bridge_epoch):
                    outcome = "bridge_lost"
                    return
                # Cancellation and commitment have one ordering point. A
                # committed send cannot be recalled; topology validation at the
                # server still applies. Never hold the input lock over transport.
                task.dispatch_started = True
                self._stage(task, "candidate_dispatch_committed")
            sent = bool(self.send_candidate(task.candidate))
            self._stage(task, "candidate_sent", sent=sent)
            outcome = "candidate_sent" if sent else "candidate_send_failed"
        except ConnectionError:
            outcome = "bridge_lost"
        except Exception as error:
            outcome = "correlation_exception"
            self._stage(task, outcome, error_type=type(error).__name__)
        finally:
            if refresh_id is not None:
                self.desktop.release_metadata_refresh(refresh_id)
            with self._lock:
                self._pending.pop(task.request_id, None)
                self._tasks.pop(task.gesture_id, None)
            if task.cancelled.is_set():
                outcome = "cancelled"
            task.stages["finished"] = self.now()
            logger.info("browser_handoff stage=gesture_summary gesture=%s outcome=%s timestamps=%s",
                        task.gesture_id[:8], outcome, task.stages)

    def handle_snapshot(self, instance_id, message):
        if type(message) is not dict or type(instance_id) is not str:
            return False
        with self._lock:
            task = self._pending.get(message.get("request_id"))
            if task is None or not self._alive(task) or task.candidate is not None or task.failure:
                return False
            matched = task.matched
            if instance_id != matched.browser_instance_id:
                return False
            snapshot = message.get("snapshot")
            reason = None
            if message.get("_bridge_epoch") != matched.bridge_epoch:
                reason = "bridge_epoch_mismatch"
            elif type(snapshot) is not dict:
                reason = "missing_snapshot"
            elif snapshot.get("window_id") != matched.window_id:
                reason = "window_mismatch"
            elif snapshot.get("revision") != matched.metadata_revision:
                reason = "revision_mismatch"
            elif message.get("epoch") is not None and message["epoch"] != instance_id:
                reason = "instance_epoch_mismatch"
            elif snapshot.get("complete_capture") is not True:
                reason = "incomplete"
            elif snapshot.get("incognito") is True:
                reason = "incognito_unsupported"
            if reason:
                task.failure = reason
                self._stage(task, "snapshot_rejected", reason=reason)
                return False
            self._stage(task, "snapshot_received")
            task.candidate = {
                "protocol": 1, "gesture_id": task.gesture_id,
                "source_display_id": task.source_display_id, "source_side": task.source_side,
                "topology_version": task.topology_version,
                "incognito": snapshot.get("incognito") is True,
                "total_count": snapshot.get("total_count"), "entries": snapshot.get("entries"),
                "complete_capture": True,
            }
            self._stage(task, "candidate_ready", tabs=snapshot.get("total_count"))
            return True

    def cancel(self):
        with self._lock:
            for task in self._tasks.values():
                if not task.dispatch_started:
                    task.cancelled.set()
            self._pending.clear()
            self._tasks = {key: task for key, task in self._tasks.items() if task.dispatch_started}

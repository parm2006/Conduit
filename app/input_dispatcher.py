"""Bounded per-session delivery for remote mouse and keyboard input."""

from collections import deque
from dataclasses import dataclass, field
import logging
import threading
import time
import math


logger = logging.getLogger(__name__)


@dataclass
class _SessionQueue:
    session_id: str
    condition: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.Lock())
    )
    records: deque = field(default_factory=deque)
    pending_movement: int = 0
    pending_discrete: int = 0
    accepting: bool = True
    failed: bool = False
    worker: threading.Thread | None = None
    timings: deque = field(default_factory=lambda: deque(maxlen=256))
    completed: int = 0
    failed_sends: int = 0
    coalesced_positions: int = 0


class InputDispatcher:
    """Keep remote input socket writes off GUI and input-hook threads."""

    def __init__(
        self,
        *,
        lane_for_session,
        on_failure,
        max_movement=512,
        max_discrete=256,
        max_batch=32,
        clock=time.monotonic,
    ):
        self._lane_for_session = lane_for_session
        self._on_failure = on_failure
        self._max_movement = int(max_movement)
        self._max_discrete = int(max_discrete)
        self._max_batch = int(max_batch)
        self._clock = clock
        self._sessions = {}
        self._sessions_lock = threading.RLock()

    def start_session(self, session_id):
        if not isinstance(session_id, str) or not session_id:
            return False
        with self._sessions_lock:
            current = self._sessions.get(session_id)
            if current is not None and current.accepting:
                return True
            state = _SessionQueue(session_id)
            worker = threading.Thread(
                target=self._run_session,
                args=(state,),
                name=f"input-dispatch-{session_id[:8]}",
                daemon=True,
            )
            state.worker = worker
            self._sessions[session_id] = state
            worker.start()
            return True

    def stop_session(self, session_id):
        with self._sessions_lock:
            state = self._sessions.get(session_id)
        if state is None:
            return False
        with state.condition:
            was_accepting = state.accepting
            state.accepting = False
            state.records.clear()
            state.pending_movement = 0
            state.pending_discrete = 0
            state.condition.notify_all()
        return was_accepting

    def stop_all(self):
        with self._sessions_lock:
            session_ids = tuple(self._sessions)
        stopped = False
        for session_id in session_ids:
            stopped = self.stop_session(session_id) or stopped
        return stopped

    def enqueue_move(self, session_id, dx, dy):
        state = self._state(session_id)
        if state is None:
            return False
        failure_reason = None
        with state.condition:
            if not state.accepting:
                return False
            if state.pending_movement >= self._max_movement:
                failure_reason = "movement queue overflow"
                self._mark_failed_locked(state)
            else:
                delta = (dx, dy)
                if (
                    state.records
                    and state.records[-1][0] == "movement"
                    and len(state.records[-1][1]) < self._max_batch
                ):
                    state.records[-1][1].append(delta)
                else:
                    state.records.append(("movement", [delta], self._clock()))
                state.pending_movement += 1
                state.condition.notify()
                return True
        self._report_failure(state.session_id, failure_reason)
        return False

    def enqueue_position(self, session_id, x, y):
        state = self._state(session_id)
        if state is None:
            return False
        failure_reason = None
        with state.condition:
            if not state.accepting:
                return False
            # Coalesce: if the last queued record is already a position, update it in-place!
            if state.records and state.records[-1][0] == "position":
                state.records[-1] = ("position", (int(x), int(y)), self._clock())
                state.coalesced_positions += 1
                return True
            if state.pending_movement >= self._max_movement:
                failure_reason = "position queue overflow"
                self._mark_failed_locked(state)
            else:
                state.records.append(("position", (int(x), int(y)), self._clock()))
                state.pending_movement += 1
                state.condition.notify()
                return True
        self._report_failure(state.session_id, failure_reason)
        return False

    def enqueue_discrete(self, session_id, message):
        state = self._state(session_id)
        if state is None:
            return False
        failure_reason = None
        with state.condition:
            if not state.accepting:
                return False
            if state.pending_discrete >= self._max_discrete:
                failure_reason = "discrete queue overflow"
                self._mark_failed_locked(state)
            else:
                state.records.append(("discrete", dict(message), self._clock()))
                state.pending_discrete += 1
                state.condition.notify()
                return True
        self._report_failure(state.session_id, failure_reason)
        return False

    def _state(self, session_id):
        with self._sessions_lock:
            return self._sessions.get(session_id)

    def latency_snapshot(self, session_id):
        """Return local queue/send milliseconds over the latest 256 sends.

        This measures application delivery attempts, not remote input latency.
        Positions use their newest coalesced timestamp; relative batches use
        the oldest event's timestamp. No payloads or per-event logs are kept.
        """
        state = self._state(session_id)
        if state is None:
            return None
        with state.condition:
            samples = list(state.timings)
            result = {
                "completed": state.completed,
                "failed": state.failed_sends,
                "coalesced_positions": state.coalesced_positions,
                "sample_count": len(samples),
                "pending_movement": state.pending_movement,
                "pending_discrete": state.pending_discrete,
            }
        for index, name in enumerate(("queue_ms", "send_ms")):
            values = sorted(sample[index] * 1000 for sample in samples)
            result[name] = {
                "p50": values[math.ceil(len(values) * .50) - 1] if values else 0.0,
                "p95": values[math.ceil(len(values) * .95) - 1] if values else 0.0,
                "max": values[-1] if values else 0.0,
            }
        return result

    def _run_session(self, state):
        while True:
            with state.condition:
                state.condition.wait_for(
                    lambda: bool(state.records) or not state.accepting
                )
                if not state.records:
                    return
                kind, payload, queued_at = state.records.popleft()
                if kind == "movement":
                    state.pending_movement -= len(payload)
                    message = {
                        "type": "mouse_move_batch",
                        "deltas": [list(delta) for delta in payload],
                    }
                elif kind == "position":
                    state.pending_movement -= 1
                    message = {
                        "type": "mouse_position",
                        "x": payload[0],
                        "y": payload[1],
                    }
                else:
                    state.pending_discrete -= 1
                    message = payload

            send_started = self._clock()
            try:
                lane = self._lane_for_session(state.session_id)
                sent = lane is not None and bool(lane.send_message(message))
            except Exception as exc:
                logger.warning(
                    "[cursor] Input dispatch raised for session=%s (%s)",
                    state.session_id[:8],
                    type(exc).__name__,
                )
                sent = False
            send_finished = self._clock()
            with state.condition:
                state.timings.append((
                    max(0.0, send_started - queued_at),
                    max(0.0, send_finished - send_started),
                ))
                state.completed += 1
                state.failed_sends += int(not sent)
                state.condition.notify_all()
            if sent:
                continue
            if self._fail_state(state):
                self._report_failure(state.session_id, "input send failed")
            return

    def _fail_state(self, state):
        with state.condition:
            if not state.accepting or state.failed:
                return False
            self._mark_failed_locked(state)
            return True

    @staticmethod
    def _mark_failed_locked(state):
        state.accepting = False
        state.failed = True
        state.records.clear()
        state.pending_movement = 0
        state.pending_discrete = 0
        state.condition.notify_all()

    def _report_failure(self, session_id, reason):
        callback = self._on_failure
        if callback is None:
            return

        def report():
            try:
                callback(session_id, reason)
            except Exception as exc:
                logger.error(
                    "[cursor] Input dispatch failure callback raised (%s)",
                    type(exc).__name__,
                )

        threading.Thread(
            target=report,
            name=f"input-dispatch-failure-{session_id[:8]}",
            daemon=True,
        ).start()

import secrets
import threading
import time
from dataclasses import dataclass
from enum import Enum


class RequestState(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass
class ManifestRequest:
    request_id: str
    deadline: float
    state: RequestState = RequestState.PENDING
    manifest: dict | None = None
    error: str | None = None


class ManifestHandshakeQueue:
    def __init__(
        self, send_request, clock=time.monotonic, timeout_seconds=1.0,
        max_pending=8, history_limit=64, timer_factory=threading.Timer,
    ):
        self.send_request = send_request
        self.clock = clock
        self.timeout_seconds = float(timeout_seconds)
        self.max_pending = int(max_pending)
        self.history_limit = int(history_limit)
        self.timer_factory = timer_factory
        self._requests = []
        self._by_id = {}
        self._timers = {}
        self._lock = threading.RLock()

    @property
    def accepted(self):
        with self._lock:
            return tuple(
                request for request in self._requests
                if request.state is RequestState.ACCEPTED
            )

    @property
    def has_pending(self):
        with self._lock:
            return bool(self._by_id)

    def begin(self):
        with self._lock:
            self._expire_locked()
            if len(self._by_id) >= self.max_pending:
                raise RuntimeError("pending manifest request limit reached")
            request = ManifestRequest(
                request_id=secrets.token_hex(16),
                deadline=self.clock() + self.timeout_seconds,
            )
            self._requests.append(request)
            self._by_id[request.request_id] = request
            self._schedule_expiry_locked(request)
        self.send_request({"type": "file_manifest_request", "request_id": request.request_id})
        return request

    def accept(self, request_id, manifest):
        with self._lock:
            request = self._by_id.get(request_id)
            if request is None or request.state is not RequestState.PENDING:
                return False
            if self.clock() > request.deadline:
                self._finish_locked(request, RequestState.TIMED_OUT)
                return False
            self._finish_locked(request, RequestState.ACCEPTED)
            return True

    def fail(self, request_id, error):
        with self._lock:
            request = self._by_id.get(request_id)
            if request is None or request.state is not RequestState.PENDING:
                return False
            request.error = error
            self._finish_locked(request, RequestState.FAILED)
            return True

    def expire(self):
        with self._lock:
            return self._expire_locked()

    def _expire_locked(self):
        now = self.clock()
        expired = []
        for request in tuple(self._by_id.values()):
            if now > request.deadline:
                self._finish_locked(request, RequestState.TIMED_OUT)
                expired.append(request)
        return expired

    def _expire_request(self, request_id):
        with self._lock:
            request = self._by_id.get(request_id)
            if request is None:
                return
            remaining = request.deadline - self.clock()
            if remaining > 0:
                self._schedule_expiry_locked(request, remaining)
                return
            self._finish_locked(request, RequestState.TIMED_OUT)

    def _schedule_expiry_locked(self, request, delay=None):
        timer = self.timer_factory(
            self.timeout_seconds if delay is None else delay,
            lambda: self._expire_request(request.request_id),
        )
        if hasattr(timer, "daemon"):
            timer.daemon = True
        self._timers[request.request_id] = timer
        timer.start()

    def _finish_locked(self, request, state):
        request.state = state
        self._by_id.pop(request.request_id, None)
        timer = self._timers.pop(request.request_id, None)
        if timer is not None:
            timer.cancel()
        self._trim_history_locked()

    def _trim_history_locked(self):
        while len(self._requests) > self.history_limit:
            for index, request in enumerate(self._requests):
                if request.state is not RequestState.PENDING:
                    self._requests.pop(index)
                    break
            else:
                break

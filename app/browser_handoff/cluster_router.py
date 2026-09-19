"""Server-owned authenticated routing for browser-handoff jobs.

The router deliberately has no socket or GUI dependency: authenticated session
identity enters as an argument, and actual sends are scheduled outside its
lock so a cursor edge cannot wait for browser or network work.
"""

from dataclasses import dataclass
import json
import logging
import secrets
import threading

from .protocol import (
    BrowserHandoffProtocolError,
    MAX_ACTIVE_REQUESTS_PER_NODE,
    MAX_REQUEST_BYTES,
    MAX_TERMINAL_RESULTS_PER_EPOCH,
    OPERATION_DEADLINE_SECONDS,
    RESULT_RETENTION_SECONDS,
    ROUTE_TICKET_TTL_SECONDS,
    validate_candidate,
    validate_request,
    validate_result,
)


MAX_STAGED_CANDIDATES_PER_SOURCE = 8
MAX_STAGED_CANDIDATE_BYTES_PER_SOURCE = 4 * MAX_REQUEST_BYTES
MAX_STAGED_CANDIDATES = 64
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserCapability:
    session_id: str
    machine_id: str
    receiver_epoch: str
    browser_instance_id: str


@dataclass
class BrowserRoute:
    ticket: str
    source_session_id: str
    source_machine_id: str
    destination_session_id: str
    destination_machine_id: str
    topology_version: int
    expires_at: float
    gesture_id: str | None = None
    source_display_id: str | None = None
    source_side: str | None = None
    request_id: str | None = None
    deadline_at: float | None = None


class ClusterBrowserRouter:
    """Authorize at an accepted edge and relay only its matching messages."""

    def __init__(
        self,
        *,
        server_session_id,
        endpoint_available,
        send,
        now,
        enqueue=None,
        max_active=MAX_ACTIVE_REQUESTS_PER_NODE,
        max_results=MAX_TERMINAL_RESULTS_PER_EPOCH,
        max_staged_candidates_per_source=MAX_STAGED_CANDIDATES_PER_SOURCE,
        max_staged_candidate_bytes_per_source=MAX_STAGED_CANDIDATE_BYTES_PER_SOURCE,
        max_staged_candidates=MAX_STAGED_CANDIDATES,
    ):
        if not isinstance(server_session_id, str) or not server_session_id:
            raise ValueError("server_session_id is invalid")
        if type(max_active) is not int or max_active < 1:
            raise ValueError("max_active is invalid")
        self.server_session_id = server_session_id
        self.endpoint_available = endpoint_available
        self.send = send
        self.now = now
        self.enqueue = enqueue or self._enqueue_daemon
        self.max_active = max_active
        self.max_results = max_results
        self.max_staged_candidates_per_source = int(max_staged_candidates_per_source)
        self.max_staged_candidate_bytes_per_source = int(max_staged_candidate_bytes_per_source)
        self.max_staged_candidates = int(max_staged_candidates)
        self._capabilities = {}
        self._routes = {}
        self._candidates = {}
        self._active = {}
        self._terminal = {}
        self._topology_version = None
        self._stopped = False
        self._lock = threading.RLock()

    def register_capability(self, session_id, machine_id, receiver_epoch, browser_instance_id="default"):
        if not all(isinstance(value, str) and value for value in (session_id, machine_id, receiver_epoch, browser_instance_id)):
            return False
        if any(len(value) > 256 for value in (session_id, machine_id, receiver_epoch, browser_instance_id)):
            return False
        dispatches = []
        with self._lock:
            if self._stopped:
                return False
            previous = self._capabilities.get(session_id)
            self._capabilities[session_id] = BrowserCapability(session_id, machine_id, receiver_epoch, browser_instance_id)
            if previous is not None and previous != self._capabilities[session_id]:
                self._invalidate_session_locked(session_id)
            for route in tuple(self._routes.values()):
                if route.destination_session_id != session_id or route.gesture_id is None:
                    continue
                record = self._candidates.get((route.source_session_id, route.gesture_id))
                dispatch = self._dispatch_candidate_locked(route, record) if record is not None else None
                if dispatch is not None:
                    dispatches.append(dispatch)
        for route, outbound in dispatches:
            self.enqueue(lambda route=route, outbound=outbound: self._deliver_request(route, outbound))
        return True

    def authorize_edge(
        self, source_session_id, source_machine_id,
        destination_session_id, destination_machine_id, topology_version,
        *, gesture_id=None, source_display_id=None, source_side=None,
    ):
        if not all(isinstance(value, str) and value for value in (
            source_session_id, source_machine_id, destination_session_id, destination_machine_id,
        )) or type(topology_version) is not int or topology_version < 0:
            return None
        if gesture_id is not None and (type(gesture_id) is not str or not gesture_id):
            return None
        if source_display_id is not None and (type(source_display_id) is not str or not source_display_id):
            return None
        if source_side is not None and source_side not in {"left", "right", "top", "bottom"}:
            return None
        dispatch = None
        with self._lock:
            if self._stopped or not self.endpoint_available(destination_session_id):
                return None
            destination = self._capabilities.get(destination_session_id)
            if destination is None or destination.machine_id != destination_machine_id:
                return None
            if source_session_id != self.server_session_id:
                source = self._capabilities.get(source_session_id)
                if source is None or source.machine_id != source_machine_id:
                    return None
            self._topology_version = topology_version
            ticket = secrets.token_hex(16)
            self._routes[ticket] = BrowserRoute(
                ticket, source_session_id, source_machine_id,
                destination_session_id, destination_machine_id, topology_version,
                self.now() + ROUTE_TICKET_TTL_SECONDS,
                gesture_id=gesture_id,
                source_display_id=source_display_id,
                source_side=source_side,
            )
            if gesture_id is not None:
                record = self._candidates.get((source_session_id, gesture_id))
                if record is not None:
                    dispatch = self._dispatch_candidate_locked(self._routes[ticket], record)
        if dispatch is not None:
            route, outbound = dispatch
            self.enqueue(lambda: self._deliver_request(route, outbound))
        logger.info(
            "browser_handoff stage=edge_route_created gesture=%s destination=%s dispatched=%s",
            gesture_id, destination_session_id, dispatch is not None,
        )
        return ticket

    def stage_candidate(self, source_session_id, source_machine_id, message):
        """Stage one authenticated source snapshot and join it to an edge when available."""
        try:
            candidate = validate_candidate(message)
        except BrowserHandoffProtocolError:
            return False
        dispatch = None
        with self._lock:
            self._purge_candidates_locked()
            if self._stopped or (
                self._topology_version is not None
                and candidate.topology_version != self._topology_version
            ):
                return False
            if source_session_id != self.server_session_id:
                capability = self._capabilities.get(source_session_id)
                if capability is None or capability.machine_id != source_machine_id:
                    return False
            key = (source_session_id, candidate.gesture_id)
            if key in self._candidates:
                return False
            if len(self._candidates) >= self.max_staged_candidates:
                return False
            candidate_bytes = _encoded_candidate_size(message)
            source_records = [
                value for (session_id, _gesture_id), value in self._candidates.items()
                if session_id == source_session_id
            ]
            if (
                len(source_records) >= self.max_staged_candidates_per_source
                or sum(value[3] for value in source_records) + candidate_bytes
                > self.max_staged_candidate_bytes_per_source
            ):
                return False
            record = (
                source_machine_id, candidate, self.now() + ROUTE_TICKET_TTL_SECONDS,
                candidate_bytes,
            )
            self._candidates[key] = record
            route = next(
                (
                    value for value in self._routes.values()
                    if value.source_session_id == source_session_id
                    and value.gesture_id == candidate.gesture_id
                ),
                None,
            )
            if route is not None:
                dispatch = self._dispatch_candidate_locked(route, record)
        if dispatch is not None:
            route, outbound = dispatch
            self.enqueue(lambda: self._deliver_request(route, outbound))
        logger.info(
            "browser_handoff stage=candidate_staged gesture=%s source=%s dispatched=%s",
            candidate.gesture_id, source_session_id, dispatch is not None,
        )
        return True

    def accept_request(self, source_session_id, message):
        try:
            request = validate_request(message)
        except BrowserHandoffProtocolError:
            return False
        with self._lock:
            route = self._usable_route_locked(request.route_ticket, request.topology_version)
            if route is None or route.source_session_id != source_session_id:
                return False
            if request.destination_machine_id != route.destination_machine_id:
                return False
            if route.request_id is not None:
                return False
            if len(self._active) >= self.max_active:
                return False
            if not self.endpoint_available(route.destination_session_id):
                return False
            route.request_id = request.request_id
            route.deadline_at = self.now() + OPERATION_DEADLINE_SECONDS
            self._active[request.request_id] = route
            destination = self._capabilities.get(route.destination_session_id)
            outbound = {
                "type": "browser_handoff_request", "request": dict(message),
                "browser_instance_id": destination.browser_instance_id,
            }
        self.enqueue(lambda: self._deliver_request(route, outbound))
        return True

    def accept_result(self, receiver_session_id, message):
        try:
            result = validate_result(message)
        except BrowserHandoffProtocolError:
            return False
        with self._lock:
            route = self._active.get(result.request_id)
            if route is None or route.ticket != result.route_ticket:
                return False
            if route.destination_session_id != receiver_session_id:
                return False
            capability = self._capabilities.get(receiver_session_id)
            if capability is None or capability.receiver_epoch != result.receiver_epoch:
                return False
            if route.deadline_at is not None and self.now() > route.deadline_at:
                self._finish_locked(route, self._unknown_result(route))
                return False
            self._finish_locked(route, dict(message))
            outbound = {"type": "browser_handoff_result", **dict(message)}
        self.enqueue(lambda: self._safe_send(route.source_session_id, outbound))
        return True

    def topology_changed(self, version):
        if type(version) is not int or version < 0:
            return False
        with self._lock:
            self._topology_version = version
            self._purge_candidates_locked()
            for key, (_machine_id, candidate, _expires_at, _candidate_bytes) in tuple(self._candidates.items()):
                if candidate.topology_version != version:
                    self._candidates.pop(key, None)
            for ticket, route in tuple(self._routes.items()):
                if route.topology_version != version:
                    self._routes.pop(ticket, None)
                    if route.request_id is not None:
                        self._finish_locked(route, self._unknown_result(route))
        return True

    def endpoint_disconnected(self, session_id):
        with self._lock:
            self._capabilities.pop(session_id, None)
            for key in tuple(self._candidates):
                if key[0] == session_id:
                    self._candidates.pop(key, None)
            self._invalidate_session_locked(session_id)

    def stop(self):
        with self._lock:
            self._stopped = True
            for route in tuple(self._active.values()):
                self._finish_locked(route, self._unknown_result(route))
            self._routes.clear()
            self._candidates.clear()
            self._capabilities.clear()

    def status(self, request_id):
        with self._lock:
            terminal = self._terminal.get(request_id)
            if terminal is not None:
                return dict(terminal[1])
            route = self._active.get(request_id)
            if route is not None and route.deadline_at is not None and self.now() > route.deadline_at:
                self._finish_locked(route, self._unknown_result(route))
                return dict(self._terminal[request_id][1])
        return None

    def _deliver_request(self, route, outbound):
        if not self._safe_send(route.destination_session_id, outbound):
            with self._lock:
                if self._active.get(route.request_id) is route:
                    self._finish_locked(route, self._unknown_result(route))

    def _dispatch_candidate_locked(self, route, record):
        if route is None or route.request_id is not None:
            return None
        _source_machine_id, candidate, expires_at, _candidate_bytes = record
        key = (route.source_session_id, candidate.gesture_id)
        if expires_at < self.now() or route.expires_at < self.now():
            self._candidates.pop(key, None)
            return None
        if route.topology_version != candidate.topology_version:
            self._candidates.pop(key, None)
            return None
        if route.source_display_id is not None and route.source_display_id != candidate.source_display_id:
            return None
        if route.source_side is not None and route.source_side != candidate.source_side:
            return None
        if len(self._active) >= self.max_active or not self.endpoint_available(route.destination_session_id):
            return None
        destination = self._capabilities.get(route.destination_session_id)
        if destination is None:
            return None
        request_id = secrets.token_hex(16)
        request = {
            "protocol": 1,
            "request_id": request_id,
            "route_ticket": route.ticket,
            "topology_version": route.topology_version,
            "destination_machine_id": route.destination_machine_id,
            "incognito": candidate.incognito,
            "total_count": candidate.total_count,
            "entries": [
                {"source_index": entry.source_index, "url": entry.url, "active": entry.active}
                for entry in candidate.entries
            ],
            "complete_capture": candidate.complete_capture,
        }
        route.request_id = request_id
        route.deadline_at = self.now() + OPERATION_DEADLINE_SECONDS
        self._active[request_id] = route
        self._candidates.pop(key, None)
        logger.info(
            "browser_handoff stage=request_dispatched gesture=%s request=%s destination=%s",
            candidate.gesture_id, request_id, route.destination_session_id,
        )
        return route, {
            "type": "browser_handoff_request",
            "request": request,
            "browser_instance_id": destination.browser_instance_id,
        }

    def _usable_route_locked(self, ticket, topology_version):
        route = self._routes.get(ticket)
        if (
            route is None or self._stopped or route.expires_at < self.now() or
            route.topology_version != topology_version or
            (self._topology_version is not None and route.topology_version != self._topology_version)
        ):
            return None
        return route

    def _purge_candidates_locked(self):
        now = self.now()
        for key, record in tuple(self._candidates.items()):
            if record[2] <= now:
                self._candidates.pop(key, None)

    def _invalidate_session_locked(self, session_id):
        for key in tuple(self._candidates):
            if key[0] == session_id:
                self._candidates.pop(key, None)
        for ticket, route in tuple(self._routes.items()):
            if session_id in {route.source_session_id, route.destination_session_id}:
                self._routes.pop(ticket, None)
                if route.request_id is not None:
                    self._finish_locked(route, self._unknown_result(route))

    def _finish_locked(self, route, result):
        if route.request_id is None:
            return
        self._active.pop(route.request_id, None)
        self._routes.pop(route.ticket, None)
        self._terminal.pop(route.request_id, None)
        self._terminal[route.request_id] = (self.now() + RESULT_RETENTION_SECONDS, dict(result))
        while len(self._terminal) > self.max_results:
            self._terminal.pop(next(iter(self._terminal)))
        for request_id, (expires_at, _result) in tuple(self._terminal.items()):
            if expires_at < self.now():
                self._terminal.pop(request_id, None)

    @staticmethod
    def _unknown_result(route):
        return {
            "request_id": route.request_id, "route_ticket": route.ticket,
            "status": "unknown", "opened_count": 0, "total_count": 0,
            "entries": [], "receiver_epoch": "router-expired",
        }

    def _safe_send(self, session_id, message):
        try:
            return bool(self.send(session_id, message))
        except Exception:
            return False

    @staticmethod
    def _enqueue_daemon(task):
        thread = threading.Thread(target=task, name="browser-handoff-route", daemon=True)
        thread.start()


def _encoded_candidate_size(message):
    """Return the bounded wire size already checked by candidate validation."""
    return len(json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))

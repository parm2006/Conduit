"""Server-owned authenticated routing for browser-handoff jobs.

The router deliberately has no socket or GUI dependency: authenticated session
identity enters as an argument, and actual sends are scheduled outside its
lock so a cursor edge cannot wait for browser or network work.
"""

from dataclasses import dataclass
import secrets
import threading

from .protocol import (
    BrowserHandoffProtocolError,
    MAX_ACTIVE_REQUESTS_PER_NODE,
    MAX_TERMINAL_RESULTS_PER_EPOCH,
    OPERATION_DEADLINE_SECONDS,
    RESULT_RETENTION_SECONDS,
    ROUTE_TICKET_TTL_SECONDS,
    validate_request,
    validate_result,
)


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
        self._capabilities = {}
        self._routes = {}
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
        with self._lock:
            if self._stopped:
                return False
            previous = self._capabilities.get(session_id)
            self._capabilities[session_id] = BrowserCapability(session_id, machine_id, receiver_epoch, browser_instance_id)
            if previous is not None and previous != self._capabilities[session_id]:
                self._invalidate_session_locked(session_id)
        return True

    def authorize_edge(
        self, source_session_id, source_machine_id,
        destination_session_id, destination_machine_id, topology_version,
    ):
        if not all(isinstance(value, str) and value for value in (
            source_session_id, source_machine_id, destination_session_id, destination_machine_id,
        )) or type(topology_version) is not int or topology_version < 0:
            return None
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
            )
            return ticket

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
            for ticket, route in tuple(self._routes.items()):
                if route.topology_version != version:
                    self._routes.pop(ticket, None)
                    if route.request_id is not None:
                        self._finish_locked(route, self._unknown_result(route))
        return True

    def endpoint_disconnected(self, session_id):
        with self._lock:
            self._capabilities.pop(session_id, None)
            self._invalidate_session_locked(session_id)

    def stop(self):
        with self._lock:
            self._stopped = True
            for route in tuple(self._active.values()):
                self._finish_locked(route, self._unknown_result(route))
            self._routes.clear()
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

    def _usable_route_locked(self, ticket, topology_version):
        route = self._routes.get(ticket)
        if (
            route is None or self._stopped or route.expires_at < self.now() or
            route.topology_version != topology_version or
            (self._topology_version is not None and route.topology_version != self._topology_version)
        ):
            return None
        return route

    def _invalidate_session_locked(self, session_id):
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

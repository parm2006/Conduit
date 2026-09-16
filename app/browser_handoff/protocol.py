"""Strict, bounded protocol values for cross-machine browser handoff."""

from dataclasses import dataclass
import json
import re


PROTOCOL_VERSION = 1
MAX_TABS_PER_WINDOW = 256
MAX_URL_BYTES = 16 * 1024
MAX_REQUEST_BYTES = 512 * 1024
MAX_ACTIVE_REQUESTS_PER_NODE = 8
MAX_TERMINAL_RESULTS_PER_EPOCH = 256
OPERATION_DEADLINE_SECONDS = 10
ROUTE_TICKET_TTL_SECONDS = 60
RESULT_RETENTION_SECONDS = 5 * 60
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_REQUEST_FIELDS = frozenset({
    "protocol", "request_id", "route_ticket", "topology_version",
    "destination_machine_id", "incognito", "total_count", "entries",
    "complete_capture",
})
_ENTRY_FIELDS = frozenset({"source_index", "url", "active"})
_RESULT_FIELDS = frozenset({
    "request_id", "route_ticket", "status", "opened_count", "total_count",
    "entries", "receiver_epoch",
})
_RESULT_ENTRY_FIELDS = frozenset({"source_index", "reason"})
_RESULT_STATUSES = frozenset({"complete", "partial", "failed", "unknown"})


class BrowserHandoffProtocolError(ValueError):
    """A safe, URL-free reason that a browser handoff payload was rejected."""


def _require_exact_fields(value, fields, label):
    if type(value) is not dict or set(value) != fields:
        raise BrowserHandoffProtocolError(f"invalid_{label}_fields")


def _require_int(value, label, *, minimum=None, maximum=None):
    if type(value) is not int:
        raise BrowserHandoffProtocolError(f"invalid_{label}")
    if minimum is not None and value < minimum:
        raise BrowserHandoffProtocolError(f"invalid_{label}")
    if maximum is not None and value > maximum:
        raise BrowserHandoffProtocolError(f"invalid_{label}")
    return value


def _encoded_size(value):
    try:
        return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError) as error:
        raise BrowserHandoffProtocolError("invalid_request_encoding") from error


@dataclass(frozen=True)
class HandoffEntry:
    source_index: int
    url: str
    active: bool


@dataclass(frozen=True)
class HandoffRequest:
    request_id: str
    route_ticket: str
    topology_version: int
    destination_machine_id: str
    incognito: bool
    total_count: int
    entries: tuple
    complete_capture: bool


@dataclass(frozen=True)
class HandoffResult:
    request_id: str
    route_ticket: str
    status: str
    opened_count: int
    total_count: int
    entries: tuple
    receiver_epoch: str


def validate_request(message):
    """Validate a v1 request before routing or browser API side effects."""
    _require_exact_fields(message, _REQUEST_FIELDS, "request")
    if type(message["protocol"]) is not int or message["protocol"] != PROTOCOL_VERSION:
        raise BrowserHandoffProtocolError("unsupported_protocol")
    request_id = message["request_id"]
    if type(request_id) is not str or not _REQUEST_ID.fullmatch(request_id):
        raise BrowserHandoffProtocolError("invalid_request_id")
    route_ticket = message["route_ticket"]
    if type(route_ticket) is not str or not route_ticket or len(route_ticket) > 256:
        raise BrowserHandoffProtocolError("invalid_route_ticket")
    topology_version = _require_int(message["topology_version"], "topology_version", minimum=0)
    destination_machine_id = message["destination_machine_id"]
    if type(destination_machine_id) is not str or not destination_machine_id or len(destination_machine_id) > 256:
        raise BrowserHandoffProtocolError("invalid_destination_machine")
    if type(message["incognito"]) is not bool or type(message["complete_capture"]) is not bool:
        raise BrowserHandoffProtocolError("invalid_boolean")
    total_count = _require_int(message["total_count"], "total_count", minimum=0, maximum=MAX_TABS_PER_WINDOW)
    entries_value = message["entries"]
    if type(entries_value) is not list or len(entries_value) > total_count:
        raise BrowserHandoffProtocolError("invalid_entries")

    entries = []
    source_indexes = set()
    active_count = 0
    for value in entries_value:
        _require_exact_fields(value, _ENTRY_FIELDS, "entry")
        source_index = _require_int(value["source_index"], "source_index", minimum=0, maximum=max(total_count - 1, 0))
        if source_index in source_indexes:
            raise BrowserHandoffProtocolError("duplicate_source_index")
        source_indexes.add(source_index)
        url = value["url"]
        if type(url) is not str or len(url.encode("utf-8")) > MAX_URL_BYTES:
            raise BrowserHandoffProtocolError("invalid_url")
        active = value["active"]
        if type(active) is not bool:
            raise BrowserHandoffProtocolError("invalid_active")
        active_count += active
        entries.append(HandoffEntry(source_index, url, active))
    if active_count > 1:
        raise BrowserHandoffProtocolError("multiple_active_entries")
    if message["complete_capture"] and source_indexes != set(range(total_count)):
        raise BrowserHandoffProtocolError("incomplete_capture")
    if _encoded_size(message) > MAX_REQUEST_BYTES:
        raise BrowserHandoffProtocolError("request_too_large")
    return HandoffRequest(
        request_id=request_id,
        route_ticket=route_ticket,
        topology_version=topology_version,
        destination_machine_id=destination_machine_id,
        incognito=message["incognito"],
        total_count=total_count,
        entries=tuple(entries),
        complete_capture=message["complete_capture"],
    )


def validate_result(message):
    """Validate a receiver result without accepting URLs in diagnostics."""
    _require_exact_fields(message, _RESULT_FIELDS, "result")
    request_id = message["request_id"]
    if type(request_id) is not str or not _REQUEST_ID.fullmatch(request_id):
        raise BrowserHandoffProtocolError("invalid_request_id")
    route_ticket = message["route_ticket"]
    receiver_epoch = message["receiver_epoch"]
    if any(type(value) is not str or not value or len(value) > 256 for value in (route_ticket, receiver_epoch)):
        raise BrowserHandoffProtocolError("invalid_result_identity")
    status = message["status"]
    if status not in _RESULT_STATUSES:
        raise BrowserHandoffProtocolError("invalid_result_status")
    total_count = _require_int(message["total_count"], "total_count", minimum=0, maximum=MAX_TABS_PER_WINDOW)
    opened_count = _require_int(message["opened_count"], "opened_count", minimum=0, maximum=total_count)
    entries = message["entries"]
    if type(entries) is not list or len(entries) > total_count:
        raise BrowserHandoffProtocolError("invalid_result_entries")
    indexes = set()
    reasons = []
    for entry in entries:
        _require_exact_fields(entry, _RESULT_ENTRY_FIELDS, "result_entry")
        source_index = _require_int(entry["source_index"], "source_index", minimum=0, maximum=max(total_count - 1, 0))
        reason = entry["reason"]
        if source_index in indexes or type(reason) is not str or not reason or len(reason) > 128:
            raise BrowserHandoffProtocolError("invalid_result_entry")
        indexes.add(source_index)
        reasons.append((source_index, reason))
    if status == "complete" and (opened_count != total_count or entries):
        raise BrowserHandoffProtocolError("inconsistent_complete_result")
    return HandoffResult(request_id, route_ticket, status, opened_count, total_count, tuple(reasons), receiver_epoch)

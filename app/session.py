"""One logical Conduit session shared by its independent network lanes."""

import logging
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
import uuid
from enum import Enum

logger = logging.getLogger(__name__)


def _schedule_daemon(delay, callback):
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


class SessionAuthenticationError(ValueError):
    safe_for_user = True


class AdmissionOutcome(str, Enum):
    ADMITTED = "admitted"
    PENDING = "pending"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


class CandidateDecision(str, Enum):
    REPLACE = "replace"
    REJECT = "reject"


class SessionPhase(str, Enum):
    PARTIAL = "partial"
    READY = "ready"
    CLOSED = "closed"


CLIENT_SLOT_COLORS = ("#3B82F6", "#34D399")
PENDING_CLIENT_COLOR = "#A855F7"


@dataclass
class ClientSession:
    session_id: str
    peer_identity: str
    windows_name: str
    label: str
    peer_address: str | None
    control_lane: object
    slot: int
    color: str
    created_at: float
    deadline: float
    data_lane: object = None
    file_lane: object = None
    phase: SessionPhase = SessionPhase.PARTIAL
    display_inventory: object = None
    draft_placement: object = None
    cancellation_state: object = None
    replacement_color: str | None = None

    @property
    def ready(self):
        return self.phase is SessionPhase.READY

    def bind(self, purpose, lane):
        if purpose == "data":
            self.data_lane = lane
        elif purpose == "file":
            self.file_lane = lane
        else:
            raise ValueError(f"unsupported lane purpose: {purpose}")
        if self.control_lane is not None and self.data_lane is not None and self.file_lane is not None:
            self.phase = SessionPhase.READY
            self.deadline = float("inf")

    def close(self):
        if self.phase is SessionPhase.CLOSED:
            return
        self.phase = SessionPhase.CLOSED
        seen = set()
        for lane in (self.control_lane, self.data_lane, self.file_lane):
            if lane is None or id(lane) in seen:
                continue
            seen.add(id(lane))
            closer = getattr(lane, "disconnect", None) or getattr(lane, "close", None)
            if closer is not None:
                try:
                    closer()
                except OSError:
                    pass


@dataclass(frozen=True)
class SessionAdmission:
    outcome: AdmissionOutcome
    session_id: str
    peer_identity: str
    windows_name: str
    label: str
    color: str
    data_token: str | None = None
    file_token: str | None = None
    deadline: float | None = None


@dataclass(frozen=True)
class _LaneToken:
    session_id: str
    purpose: str
    peer_identity: str
    peer_address: str | None
    expires: float


@dataclass
class _PendingCandidate:
    admission: SessionAdmission
    peer_address: str | None
    control_lane: object


class SessionRegistry:
    """Own at most two active Client lane bundles and one bounded candidate."""

    def __init__(
        self,
        password,
        *,
        clock=time.monotonic,
        token_ttl=10.0,
        lane_timeout=10.0,
        candidate_timeout=15.0,
        capacity=2,
        scheduler=None,
    ):
        self._password = str(password)
        self._clock = clock
        self._token_ttl = float(token_ttl)
        self._lane_timeout = float(lane_timeout)
        self._candidate_timeout = float(candidate_timeout)
        self._capacity = int(capacity)
        self._scheduler = scheduler or _schedule_daemon
        self._sessions = {}
        self._tokens = {}
        self._session_expiry_handles = {}
        self._pending = None
        self._candidate_expiry_handle = None
        self._candidate_events = {}
        self._candidate_resolutions = {}
        self._lock = threading.RLock()

    def authenticate_control(
        self,
        candidate,
        *,
        peer_identity,
        windows_name,
        peer_address=None,
        lane=None,
    ):
        candidate_bytes = (
            candidate.encode("utf-8")
            if isinstance(candidate, str)
            else None
        )
        expected_bytes = self._password.encode("utf-8")
        if candidate_bytes is None or not hmac.compare_digest(
            candidate_bytes,
            expected_bytes,
        ):
            logger.warning(
                "Control lane password authentication failed "
                "(Windows name=%r, peer=%r, text_value=%s, "
                "byte_length_match=%s)",
                windows_name,
                peer_address,
                candidate_bytes is not None,
                (
                    candidate_bytes is not None
                    and len(candidate_bytes) == len(expected_bytes)
                ),
            )
            raise SessionAuthenticationError("authentication failed")
        if not isinstance(peer_identity, str) or not peer_identity.strip():
            raise SessionAuthenticationError("peer identity is invalid")
        if not isinstance(windows_name, str) or not windows_name.strip():
            raise SessionAuthenticationError("Windows name is invalid")
        peer_address = self._normalize_peer_address(peer_address)
        now = self._clock()
        with self._lock:
            if len(self._sessions) >= self._capacity:
                if self._pending is not None:
                    self._close_lane(lane)
                    return self._candidate_admission(
                        AdmissionOutcome.REJECTED,
                        peer_identity,
                        windows_name,
                        self._unique_label(windows_name),
                        now,
                    )
                admission = self._candidate_admission(
                    AdmissionOutcome.PENDING,
                    peer_identity,
                    windows_name,
                    self._unique_label(windows_name),
                    now + self._candidate_timeout,
                )
                self._pending = _PendingCandidate(admission, peer_address, lane)
                self._candidate_events[admission.session_id] = threading.Event()
                self._candidate_expiry_handle = self._scheduler(
                    self._candidate_timeout,
                    self.expire,
                )
                return admission

            slot = self._next_slot()
            return self._admit_locked(
                peer_identity,
                windows_name,
                peer_address,
                lane,
                slot,
                CLIENT_SLOT_COLORS[slot - 1],
                now,
            )

    def bind_lane(
        self,
        token,
        purpose,
        session_id,
        *,
        peer_identity,
        peer_address=None,
        lane=None,
    ):
        if not isinstance(token, str) or not isinstance(session_id, str):
            raise SessionAuthenticationError("lane token is invalid")
        peer_address = self._normalize_peer_address(peer_address)
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        with self._lock:
            record = self._tokens.get(digest)
            if record is None:
                raise SessionAuthenticationError("lane token is invalid or already used")
            if self._clock() >= record.expires:
                self._tokens.pop(digest, None)
                raise SessionAuthenticationError("lane token expired")
            if (
                record.session_id != session_id
                or record.purpose != purpose
                or record.peer_identity != peer_identity
                or record.peer_address != peer_address
            ):
                raise SessionAuthenticationError(
                    "lane token belongs to another session, lane, or peer"
                )
            session = self._sessions.get(session_id)
            if session is None or session.phase is SessionPhase.CLOSED:
                raise SessionAuthenticationError("session is no longer active")
            self._tokens.pop(digest, None)
            session.bind(purpose, lane)
            if session.ready:
                self._cancel_session_expiry_locked(session_id)
            return session

    def consume_lane(
        self,
        token,
        purpose,
        session_id,
        peer_address=None,
        *,
        peer_identity=None,
        lane=None,
    ):
        session = self.get(session_id)
        if session is None:
            raise SessionAuthenticationError("session is no longer active")
        identity = peer_identity or session.peer_identity
        self.bind_lane(
            token,
            purpose,
            session_id,
            peer_identity=identity,
            peer_address=peer_address,
            lane=lane,
        )
        return True

    def get(self, session_id):
        with self._lock:
            return self._sessions.get(session_id)

    def active_sessions(self):
        with self._lock:
            return tuple(sorted(self._sessions.values(), key=lambda item: item.slot))

    def ready_sessions(self):
        return tuple(item for item in self.active_sessions() if item.ready)

    def pending_candidate(self):
        with self._lock:
            return None if self._pending is None else self._pending.admission

    @property
    def candidate_timeout(self):
        return self._candidate_timeout

    def close(self, session_id=None):
        with self._lock:
            if session_id is None:
                session_ids = tuple(self._sessions)
                for active_session_id in session_ids:
                    self._close_session_locked(active_session_id)
                if self._pending is not None:
                    pending = self._pending
                    self._pending = None
                    self._cancel_candidate_expiry_locked()
                    self._publish_candidate_resolution_locked(
                        SessionAdmission(
                            AdmissionOutcome.REJECTED,
                            pending.admission.session_id,
                            pending.admission.peer_identity,
                            pending.admission.windows_name,
                            pending.admission.label,
                            pending.admission.color,
                        )
                    )
                    self._close_lane(pending.control_lane)
                return True
            return self._close_session_locked(session_id)

    def expire(self):
        expired = []
        now = self._clock()
        with self._lock:
            for session in tuple(self._sessions.values()):
                if not session.ready and now >= session.deadline:
                    expired.append(
                        SessionAdmission(
                            AdmissionOutcome.TIMED_OUT,
                            session.session_id,
                            session.peer_identity,
                            session.windows_name,
                            session.label,
                            session.color,
                        )
                    )
                    self._close_session_locked(session.session_id)
            if self._pending is not None and now >= self._pending.admission.deadline:
                pending = self._pending
                self._close_lane(pending.control_lane)
                self._pending = None
                self._cancel_candidate_expiry_locked()
                expired.append(
                    self._publish_candidate_resolution_locked(SessionAdmission(
                        AdmissionOutcome.TIMED_OUT,
                        pending.admission.session_id,
                        pending.admission.peer_identity,
                        pending.admission.windows_name,
                        pending.admission.label,
                        pending.admission.color,
                    ))
                )
        return tuple(expired)

    def resolve_candidate(self, decision, *, replace_session_id=None):
        decision = CandidateDecision(decision)
        with self._lock:
            if self._pending is None:
                raise RuntimeError("there is no pending candidate")
            pending = self._pending
            if decision is CandidateDecision.REJECT:
                self._pending = None
                self._cancel_candidate_expiry_locked()
                self._close_lane(pending.control_lane)
                return self._publish_candidate_resolution_locked(SessionAdmission(
                    AdmissionOutcome.REJECTED,
                    pending.admission.session_id,
                    pending.admission.peer_identity,
                    pending.admission.windows_name,
                    pending.admission.label,
                    pending.admission.color,
                ))
            target = self._sessions.get(replace_session_id)
            if target is None:
                raise KeyError(replace_session_id)
            slot, color = target.slot, target.color
            self._close_session_locked(target.session_id)
            self._pending = None
            self._cancel_candidate_expiry_locked()
            admission = self._admit_locked(
                pending.admission.peer_identity,
                pending.admission.windows_name,
                pending.peer_address,
                pending.control_lane,
                slot,
                PENDING_CLIENT_COLOR,
                self._clock(),
                label=pending.admission.label,
                session_id=pending.admission.session_id,
            )
            self._sessions[admission.session_id].replacement_color = color
            return self._publish_candidate_resolution_locked(admission)

    def take_candidate_resolution(self, candidate_id):
        with self._lock:
            resolution = self._candidate_resolutions.pop(candidate_id, None)
            if resolution is not None:
                self._candidate_events.pop(candidate_id, None)
            return resolution

    def wait_candidate_resolution(self, candidate_id, timeout=None):
        with self._lock:
            event = self._candidate_events.get(candidate_id)
            if event is None:
                return self._candidate_resolutions.pop(candidate_id, None)
        if not event.wait(timeout):
            return None
        return self.take_candidate_resolution(candidate_id)

    def activate_replacement(self, session_id):
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.replacement_color is None:
                return False
            session.color = session.replacement_color
            session.replacement_color = None
            return True

    def _admit_locked(
        self,
        peer_identity,
        windows_name,
        peer_address,
        lane,
        slot,
        color,
        now,
        *,
        label=None,
        session_id=None,
    ):
        session_id = session_id or uuid.uuid4().hex
        label = label or self._unique_label(windows_name)
        session = ClientSession(
            session_id=session_id,
            peer_identity=peer_identity,
            windows_name=windows_name,
            label=label,
            peer_address=peer_address,
            control_lane=lane,
            slot=slot,
            color=color,
            created_at=now,
            deadline=now + self._lane_timeout,
        )
        self._sessions[session_id] = session
        data_token = self._issue_locked(session, "data")
        file_token = self._issue_locked(session, "file")
        self._session_expiry_handles[session_id] = self._scheduler(
            self._lane_timeout,
            self.expire,
        )
        return SessionAdmission(
            AdmissionOutcome.ADMITTED,
            session_id,
            peer_identity,
            windows_name,
            label,
            color,
            data_token,
            file_token,
            session.deadline,
        )

    def _candidate_admission(
        self,
        outcome,
        peer_identity,
        windows_name,
        label,
        deadline,
    ):
        return SessionAdmission(
            outcome,
            uuid.uuid4().hex,
            peer_identity,
            windows_name,
            label,
            PENDING_CLIENT_COLOR,
            deadline=deadline,
        )

    def _issue_locked(self, session, purpose):
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        self._tokens[digest] = _LaneToken(
            session.session_id,
            purpose,
            session.peer_identity,
            session.peer_address,
            self._clock() + self._token_ttl,
        )
        return token

    def _next_slot(self):
        used = {session.slot for session in self._sessions.values()}
        return next(slot for slot in range(1, self._capacity + 1) if slot not in used)

    def _unique_label(self, windows_name):
        occupied = {session.label.casefold() for session in self._sessions.values()}
        if self._pending is not None:
            occupied.add(self._pending.admission.label.casefold())
        if windows_name.casefold() not in occupied:
            return windows_name
        suffix = 2
        while f"{windows_name}.{suffix}".casefold() in occupied:
            suffix += 1
        return f"{windows_name}.{suffix}"

    def _close_session_locked(self, session_id):
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        self._cancel_session_expiry_locked(session_id)
        session.close()
        self._tokens = {
            digest: token
            for digest, token in self._tokens.items()
            if token.session_id != session_id
        }
        return True

    def _cancel_session_expiry_locked(self, session_id):
        handle = self._session_expiry_handles.pop(session_id, None)
        if handle is not None:
            handle.cancel()

    def _cancel_candidate_expiry_locked(self):
        handle, self._candidate_expiry_handle = (
            self._candidate_expiry_handle,
            None,
        )
        if handle is not None:
            handle.cancel()

    def _publish_candidate_resolution_locked(self, resolution):
        candidate_id = resolution.session_id
        self._candidate_resolutions[candidate_id] = resolution
        event = self._candidate_events.get(candidate_id)
        if event is not None:
            event.set()
        return resolution

    @staticmethod
    def _close_lane(lane):
        if lane is None:
            return
        closer = getattr(lane, "disconnect", None) or getattr(lane, "close", None)
        if closer is not None:
            try:
                closer()
            except OSError:
                pass

    @staticmethod
    def _normalize_peer_address(peer_address):
        if peer_address is None:
            return None
        try:
            return ipaddress.ip_address(str(peer_address)).compressed
        except ValueError as error:
            raise SessionAuthenticationError("peer address is invalid") from error

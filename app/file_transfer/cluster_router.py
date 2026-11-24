"""Server-owned file-job policy and best-effort cluster commands."""

from dataclasses import dataclass
from enum import Enum
import secrets
import threading

from .models import Manifest
from .validation import ValidationError, validate_manifest, validate_transfer_id


class ClusterJobPhase(str, Enum):
    PREPARING = "preparing"
    TRANSFERRING = "transferring"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class ClusterFileJob:
    job_id: str
    request_id: str
    offer_revision: int
    selection_sequence: int
    source_id: str
    destination_id: str
    phase: ClusterJobPhase = ClusterJobPhase.PREPARING
    manifest: Manifest | None = None


class ClusterFileRouter:
    """Latch file source/destination identity and relay only involved traffic."""

    SOURCE_FRAME_TYPES = frozenset((
        "manifest",
        "chunk",
        "file_complete",
        "job_complete",
    ))
    DESTINATION_FRAME_TYPES = frozenset((
        "chunk_received",
        "job_verified",
        "paste_progress",
    ))
    TERMINAL_PROGRESS = frozenset(("completed", "cancelled", "failed"))

    def __init__(
        self,
        server_id,
        *,
        latest_offer,
        endpoint_available,
        send_control,
        send_file,
        max_active_jobs=8,
    ):
        if not isinstance(server_id, str) or not server_id:
            raise ValueError("cluster Server ID is invalid")
        if type(max_active_jobs) is not int or max_active_jobs < 1:
            raise ValueError("cluster file job limit is invalid")
        self.server_id = server_id
        self.latest_offer = latest_offer
        self.endpoint_available = endpoint_available
        self.send_control = send_control
        self.send_file = send_file
        self.max_active_jobs = max_active_jobs
        self._jobs = {}
        self._requests = {}
        self._transfers = {}
        self._paused = False
        self._stopped = False
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

    def job(self, cluster_job_id):
        with self._lock:
            return self._jobs.get(cluster_job_id)

    def request_paste(self, destination_id, request_id):
        try:
            validate_transfer_id(request_id, "request ID")
        except ValidationError:
            return None
        with self._lock:
            if self._paused or self._stopped or len(self._jobs) >= self.max_active_jobs:
                return None
            offer = self.latest_offer()
            if (
                offer is None
                or offer.kind != "files"
                or not isinstance(offer.source_id, str)
                or offer.source_id == destination_id
                or not self.endpoint_available(offer.source_id)
                or not self.endpoint_available(destination_id)
                or request_id in self._requests
            ):
                return None
            job = ClusterFileJob(
                secrets.token_hex(16),
                request_id,
                offer.revision,
                offer.source_sequence,
                offer.source_id,
                destination_id,
            )
            self._jobs[job.job_id] = job
            self._requests[request_id] = job
            message = self._decorate(job, {
                "type": "file_manifest_request",
                "request_id": request_id,
            })
        if self._send_control(job.source_id, message):
            return job
        self._finish(job, ClusterJobPhase.FAILED)
        return None

    def on_manifest_response(self, source_id, message):
        request_id = message.get("request_id") if isinstance(message, dict) else None
        with self._lock:
            job = self._requests.get(request_id)
            if job is None or source_id != job.source_id or job.manifest is not None:
                return False
            try:
                parsed = validate_manifest(Manifest.from_wire(message.get("manifest")))
            except Exception:
                return False
            if parsed.job_id in self._transfers:
                return False
            job.manifest = parsed
            self._transfers[parsed.job_id] = job
            forwarded = self._decorate(job, {
                "type": "file_manifest_response",
                "request_id": request_id,
                "manifest": parsed.to_wire(),
            })
        if self._send_control(job.destination_id, forwarded):
            return True
        self._finish(job, ClusterJobPhase.FAILED)
        return False

    def on_manifest_failed(self, source_id, message):
        request_id = message.get("request_id") if isinstance(message, dict) else None
        with self._lock:
            job = self._requests.get(request_id)
            if job is None or source_id != job.source_id:
                return False
            forwarded = self._decorate(job, {
                "type": "file_manifest_failed",
                "request_id": request_id,
                "error": message.get("error", "ManifestFailed"),
            })
        self._send_control(job.destination_id, forwarded)
        self._finish(job, ClusterJobPhase.FAILED)
        return True

    def on_manifest_ack(self, destination_id, message):
        transfer_job_id = message.get("job_id") if isinstance(message, dict) else None
        with self._lock:
            job = self._transfers.get(transfer_job_id)
            if job is None or destination_id != job.destination_id:
                return False
            job.phase = ClusterJobPhase.TRANSFERRING
            forwarded = self._decorate(job, {
                "type": "file_manifest_ack",
                "job_id": transfer_job_id,
            })
        if self._send_control(job.source_id, forwarded):
            return True
        self._finish(job, ClusterJobPhase.FAILED)
        return False

    def relay_frame(self, origin_id, metadata, payload=b""):
        if not isinstance(metadata, dict) or not isinstance(payload, bytes):
            return False
        transfer_job_id = self._frame_job_id(metadata)
        with self._condition:
            while self._paused and not self._stopped:
                self._condition.wait()
            job = self._transfers.get(transfer_job_id)
            if job is None or self._stopped:
                return False
            frame_type = metadata.get("type")
            if frame_type in self.SOURCE_FRAME_TYPES and origin_id == job.source_id:
                destination_id = job.destination_id
            elif (
                frame_type in self.DESTINATION_FRAME_TYPES
                and origin_id == job.destination_id
            ):
                destination_id = job.source_id
                if frame_type == "paste_progress":
                    job.phase = ClusterJobPhase.PUBLISHING
            elif frame_type in {"cancel_job", "cancel_ack"} and origin_id in {
                job.source_id,
                job.destination_id,
            }:
                destination_id = (
                    job.destination_id
                    if origin_id == job.source_id
                    else job.source_id
                )
            else:
                return False
            forwarded = self._decorate(job, metadata)
            terminal = (
                frame_type == "paste_progress"
                and metadata.get("phase") in self.TERMINAL_PROGRESS
            )
        delivered = self._send_file(destination_id, forwarded, payload)
        if terminal:
            phase = ClusterJobPhase(metadata["phase"])
            self._finish(job, phase)
        return bool(delivered)

    def endpoint_disconnected(self, endpoint_id):
        with self._lock:
            affected = tuple(
                job
                for job in self._jobs.values()
                if endpoint_id in {job.source_id, job.destination_id}
            )
        for job in affected:
            counterpart = (
                job.destination_id
                if endpoint_id == job.source_id
                else job.source_id
            )
            if self.endpoint_available(counterpart):
                if job.manifest is None:
                    if counterpart == job.destination_id:
                        self._send_control(counterpart, self._decorate(job, {
                            "type": "file_manifest_failed",
                            "request_id": job.request_id,
                            "error": "EndpointDisconnected",
                        }))
                else:
                    self._send_file(counterpart, self._decorate(job, {
                        "type": "cancel_job",
                        "job_id": job.manifest.job_id,
                        "cancellation_id": secrets.token_hex(16),
                    }), b"")
            self._finish(job, ClusterJobPhase.CANCELLED)
        return tuple(job.job_id for job in affected)

    def pause(self):
        with self._lock:
            if self._stopped:
                return False
            self._paused = True
            return True

    def resume(self):
        with self._condition:
            if self._stopped:
                return False
            self._paused = False
            self._condition.notify_all()
            return True

    def stop(self):
        with self._condition:
            jobs = tuple(self._jobs.values())
            self._stopped = True
            self._paused = True
            self._condition.notify_all()
        for job in jobs:
            self._finish(job, ClusterJobPhase.CANCELLED)
        return True

    def _finish(self, job, phase):
        with self._lock:
            current = self._jobs.get(job.job_id)
            if current is not job:
                return False
            job.phase = phase
            self._jobs.pop(job.job_id, None)
            self._requests.pop(job.request_id, None)
            if job.manifest is not None:
                self._transfers.pop(job.manifest.job_id, None)
            return True

    def _decorate(self, job, message):
        value = dict(message)
        for private_transport_field in ("session_id", "peer_identity", "addr"):
            value.pop(private_transport_field, None)
        value.update({
            "cluster_job_id": job.job_id,
            "offer_revision": job.offer_revision,
            "selection_sequence": job.selection_sequence,
            "source_id": job.source_id,
            "destination_id": job.destination_id,
        })
        return value

    @staticmethod
    def _frame_job_id(metadata):
        if metadata.get("type") == "manifest":
            manifest = metadata.get("manifest")
            return manifest.get("job_id") if isinstance(manifest, dict) else None
        return metadata.get("job_id")

    def _send_control(self, endpoint_id, message):
        try:
            return bool(self.send_control(endpoint_id, message))
        except Exception:
            return False

    def _send_file(self, endpoint_id, metadata, payload):
        try:
            return bool(self.send_file(endpoint_id, metadata, payload))
        except Exception:
            return False


@dataclass(frozen=True)
class ClusterCommandResult:
    command_id: str
    delivered: tuple[str, ...]
    failed: tuple[str, ...]


class ClusterCommandBroadcaster:
    COMMANDS = frozenset((
        "reload_connection",
        "shutdown_app",
        "set_daemon_mode",
    ))

    def __init__(
        self,
        *,
        ready_sessions,
        send,
        release_input=None,
        local_cleanup=None,
    ):
        self.ready_sessions = ready_sessions
        self.send = send
        self.release_input = release_input or (lambda: None)
        self.local_cleanup = local_cleanup or (lambda command: None)

    def broadcast(self, command_type, payload=None, command_id=None):
        if command_type not in self.COMMANDS:
            raise ValueError("cluster command type is invalid")
        command_id = command_id or secrets.token_hex(16)
        validate_transfer_id(command_id, "command ID")
        command = dict(payload or {})
        command.update({"type": command_type, "command_id": command_id})
        delivered = []
        failed = []
        self.release_input()
        try:
            for session in tuple(self.ready_sessions()):
                session_id = session.session_id
                try:
                    sent = bool(self.send(session_id, dict(command)))
                except Exception:
                    sent = False
                (delivered if sent else failed).append(session_id)
        finally:
            self.local_cleanup(dict(command))
        return ClusterCommandResult(
            command_id,
            tuple(delivered),
            tuple(failed),
        )


class ServerClusterFileLane:
    """Present the Server's local sender/receiver as one cluster endpoint."""

    FRAME_TYPES = (
        "manifest",
        "chunk",
        "file_complete",
        "job_complete",
        "chunk_received",
        "job_verified",
        "paste_progress",
        "cancel_job",
        "cancel_ack",
    )
    supports_chunk_ack = True

    def __init__(self, server_id, network, router):
        self.server_id = server_id
        self.network = network
        self.router = router
        self._callbacks = {}
        for frame_type in self.FRAME_TYPES:
            network.register_callback(frame_type, self._remote_frame)

    def register_callback(self, event_type, callback):
        self._callbacks.setdefault(event_type, []).append(callback)

    def send(self, metadata, payload=b""):
        return self.router.relay_frame(self.server_id, metadata, payload)

    def deliver_local(self, metadata, payload=b""):
        delivered = False
        for callback in tuple(self._callbacks.get(metadata.get("type"), ())):
            callback(dict(metadata), payload)
            delivered = True
        return delivered

    def _remote_frame(self, metadata, payload=b""):
        origin_id = metadata.get("peer_identity")
        if not isinstance(origin_id, str) or not origin_id:
            return False
        return self.router.relay_frame(origin_id, metadata, payload)

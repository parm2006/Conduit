import logging
import threading
import time

from .handshake import ManifestHandshakeQueue
from .executor import FifoTransferExecutor
from .validation import ValidationError, validate_transfer_id


MAX_OUTGOING_MANIFESTS = 8
OUTGOING_MANIFEST_TIMEOUT = 30.0
# Hashing large selections is preparation, not a one-second network round trip.
MANIFEST_PREPARATION_TIMEOUT = 120.0
logger = logging.getLogger(__name__)


class FilePasteService:
    def __init__(
        self,
        control,
        receiver,
        publisher,
        sender,
        snapshot_selection,
        executor=None,
        clock=time.monotonic,
        timer_factory=threading.Timer,
    ):
        self.control = control
        self.receiver = receiver
        self.publisher = publisher
        self.sender = sender
        self.snapshot_selection = snapshot_selection
        self.executor = executor or FifoTransferExecutor(sender)
        self.handshakes = ManifestHandshakeQueue(
            self.control.send_message, timeout_seconds=MANIFEST_PREPARATION_TIMEOUT
        )
        self._outgoing = {}
        self._outgoing_timers = {}
        self._outgoing_lock = threading.Lock()
        self._destination_lock = threading.RLock()
        self._pending_snapshots = 0
        self._outgoing_limit = MAX_OUTGOING_MANIFESTS
        self._outgoing_timeout = OUTGOING_MANIFEST_TIMEOUT
        self._clock = clock
        self._timer_factory = timer_factory

    def request_paste(self):
        with self._destination_lock:
            return self.handshakes.begin()

    @property
    def destination_paste_active(self):
        with self._destination_lock:
            return (
                self.handshakes.has_pending
                or self.publisher.has_pending_paste
            )

    def queue_manifest_request(self, message):
        """Reserve bounded work before moving file hashing off the control thread."""
        return self._accept_manifest_request(message, background=True)

    def on_manifest_request(self, message):
        # Synchronous entry point for local tooling; production dispatch queues it.
        return self._accept_manifest_request(message, background=False)

    def _accept_manifest_request(self, message, *, background):
        request_id = message.get("request_id")
        try:
            validate_transfer_id(request_id, "request ID")
        except ValidationError:
            return False
        with self._outgoing_lock:
            at_limit = (
                len(self._outgoing) + self._pending_snapshots
                >= self._outgoing_limit
            )
            if not at_limit:
                self._pending_snapshots += 1
        if at_limit:
            self.control.send_message({
                "type": "file_manifest_failed",
                "request_id": request_id,
                "error": "ManifestLimitError",
            })
            return False
        generation = getattr(self.control, "_generation", None)
        if not background:
            return self._prepare_manifest(request_id, generation)
        worker = threading.Thread(
            target=self._prepare_manifest, args=(request_id, generation),
            name="file-manifest-preparation", daemon=True,
        )
        try:
            worker.start()
        except RuntimeError:
            with self._outgoing_lock:
                self._pending_snapshots -= 1
            self.control.send_message({"type": "file_manifest_failed",
                "request_id": request_id, "error": "PreparationStartFailed"})
            return False
        return True

    def _preparation_is_current(self, generation):
        return (getattr(self.control, "connected", True)
                and (generation is None or getattr(self.control, "_generation", None) == generation))

    def _prepare_manifest(self, request_id, generation):
        started = time.monotonic()
        try:
            if not self._preparation_is_current(generation):
                return False
            manifest, sources = self.snapshot_selection()
            if not self._preparation_is_current(generation):
                logger.info("Discarding manifest prepared for a replaced connection")
                return False
            validate_transfer_id(manifest.job_id)
            with self._outgoing_lock:
                if manifest.job_id in self._outgoing:
                    raise ValueError("job ID is already awaiting acknowledgement")
                deadline = self._clock() + self._outgoing_timeout
                self._outgoing[manifest.job_id] = (manifest, sources, deadline)
                self._schedule_outgoing_expiry_locked(manifest.job_id)
            sent = self.control.send_message({
                "type": "file_manifest_response",
                "request_id": request_id,
                "manifest": manifest.to_wire(),
            })
            if sent is False:
                with self._outgoing_lock:
                    self._outgoing.pop(manifest.job_id, None)
                    timer = self._outgoing_timers.pop(manifest.job_id, None)
                    if timer is not None:
                        timer.cancel()
                return False
            logger.info("File manifest prepared (bytes=%s elapsed=%.3fs)",
                        getattr(manifest, "total_size", "unknown"), time.monotonic() - started)
            return True
        except Exception as error:
            logger.warning("File manifest preparation failed (%s)", type(error).__name__)
            if self._preparation_is_current(generation):
                try:
                    self.control.send_message({
                        "type": "file_manifest_failed", "request_id": request_id,
                        "error": type(error).__name__,
                    })
                except Exception:
                    logger.warning("Could not report manifest preparation failure")
            return False
        finally:
            with self._outgoing_lock:
                self._pending_snapshots -= 1

    def on_manifest_response(self, message):
        with self._destination_lock:
            request_id = message.get("request_id")
            manifest = message.get("manifest")
            if not self.handshakes.accept(request_id, manifest):
                return False
            self.receiver.accept_manifest(manifest)
            self.control.send_message({
                "type": "file_manifest_ack",
                "job_id": manifest["job_id"],
            })
            self.publisher.publish_and_paste(manifest, self.receiver)
            return True

    def on_manifest_failed(self, message):
        with self._destination_lock:
            return self.handshakes.fail(
                message.get("request_id"), message.get("error", "failed")
            )

    def on_manifest_ack(self, message):
        job_id = message.get("job_id")
        try:
            validate_transfer_id(job_id)
        except ValidationError:
            return False
        with self._outgoing_lock:
            outgoing = self._outgoing.pop(job_id, None)
            timer = self._outgoing_timers.pop(job_id, None)
            if timer is not None:
                timer.cancel()
        if outgoing is None:
            return False
        manifest, sources, deadline = outgoing
        if self._clock() > deadline:
            return False
        self.executor.submit(manifest, sources)
        return True

    def _schedule_outgoing_expiry_locked(self, job_id):
        timer = self._timer_factory(
            self._outgoing_timeout,
            lambda: self._expire_outgoing(job_id),
        )
        if hasattr(timer, "daemon"):
            timer.daemon = True
        self._outgoing_timers[job_id] = timer
        timer.start()

    def _expire_outgoing(self, job_id):
        with self._outgoing_lock:
            outgoing = self._outgoing.get(job_id)
            if outgoing is None:
                return False
            remaining = outgoing[2] - self._clock()
            if remaining > 0:
                timer = self._timer_factory(
                    remaining, lambda: self._expire_outgoing(job_id)
                )
                if hasattr(timer, "daemon"):
                    timer.daemon = True
                self._outgoing_timers[job_id] = timer
                timer.start()
                return False
            self._outgoing.pop(job_id, None)
            self._outgoing_timers.pop(job_id, None)
            return True

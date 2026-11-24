from collections import OrderedDict
from .compression import encode_chunk, should_compress
from .models import ItemType
from .validation import ValidationError, validate_manifest, validate_transfer_id
from .status import TransferPhase
from .controller import TransferCancelled
import time
import threading


class DestinationPasteError(RuntimeError):
    pass


SAFE_DESTINATION_ERROR_CODES = frozenset((
    "ClipboardPublishFailed",
    "PasteInjectionFailed",
    "ExplorerStartTimeout",
))
MAX_IN_FLIGHT_FILE_BYTES = 1 << 20


def _safe_destination_error_code(value):
    return value if value in SAFE_DESTINATION_ERROR_CODES else "DestinationPasteFailed"


class TransferSender:
    def __init__(
        self, lane, controller=None, clock=time.monotonic,
        max_in_flight_bytes=MAX_IN_FLIGHT_FILE_BYTES,
    ):
        if max_in_flight_bytes <= 0:
            raise ValueError("file transfer window must be positive")
        self.lane = lane
        self.controller = controller
        self.clock = clock
        self.max_in_flight_bytes = int(max_in_flight_bytes)
        self._verified = {}
        self._paste_jobs = {}
        self._early_paste_terminal = OrderedDict()
        self._early_paste_limit = 256
        self._paste_lock = threading.Lock()
        self._chunk_acks = {}
        self._chunk_ack_condition = threading.Condition()
        self._in_flight_bytes = 0
        if hasattr(lane, "register_callback"):
            lane.register_callback("job_verified", self._on_verified)
            lane.register_callback("paste_progress", self._on_paste_progress)
            lane.register_callback("chunk_received", self._on_chunk_received)

    def send_job(self, manifest, sources, announce_manifest=True):
        validate_manifest(manifest)
        label = _manifest_label(manifest)
        failure_label = label
        bytes_done = 0
        with self._paste_lock:
            self._paste_jobs[manifest.job_id] = (label, manifest.total_size)
            early_terminal = self._early_paste_terminal.pop(manifest.job_id, None)
        self._waiting(manifest, label)
        if announce_manifest:
            self.lane.send({"type": "manifest", "manifest": manifest.to_wire()})
        try:
            if early_terminal is not None:
                self._on_paste_progress(early_terminal, b"")
                phase = TransferPhase(early_terminal["phase"])
                if phase is TransferPhase.CANCELLED:
                    raise TransferCancelled(manifest.job_id)
                raise DestinationPasteError(
                    _safe_destination_error_code(early_terminal.get("error_code"))
                )
            for item in manifest.items:
                if item.item_type is ItemType.DIRECTORY:
                    continue
                failure_label = item.relative_path.rsplit("/", 1)[-1]
                source = sources[item.relative_path]
                if source.size != item.size or source.sha256 != item.sha256:
                    raise ValueError("source snapshot does not match the manifest")
                chunks = iter(source.iter_chunks())
                first = next(chunks, None)
                compress = bool(first) and should_compress(item.relative_path, item.size, first)
                offset = 0
                for chunk in (() if first is None else (first,)):
                    self._check_cancelled(manifest.job_id)
                    offset = self._send_chunk(manifest.job_id, item.relative_path, offset, chunk, compress)
                    bytes_done += len(chunk)
                for chunk in chunks:
                    self._check_cancelled(manifest.job_id)
                    offset = self._send_chunk(manifest.job_id, item.relative_path, offset, chunk, compress)
                    bytes_done += len(chunk)
                self._wait_for_chunk_acks(manifest.job_id, item.relative_path)
                self.lane.send({
                    "type": "file_complete",
                    "job_id": manifest.job_id,
                    "relative_path": item.relative_path,
                })
            failure_label = label
            verified = threading.Event()
            self._verified[manifest.job_id] = verified
            self.lane.send({"type": "job_complete", "job_id": manifest.job_id})
            deadline = self.clock() + 30
            while not verified.wait(0.05):
                self._check_cancelled(manifest.job_id)
                if self.clock() >= deadline:
                    raise TimeoutError("destination did not verify the file transfer")
            self._waiting(manifest, label)
        except TransferCancelled:
            with self._paste_lock:
                self._paste_jobs.pop(manifest.job_id, None)
            raise
        except Exception as error:
            with self._paste_lock:
                self._paste_jobs.pop(manifest.job_id, None)
            if self.controller:
                self.controller.update(
                    manifest.job_id, TransferPhase.FAILED, failure_label, bytes_done,
                    manifest.total_size, error_code=type(error).__name__,
                )
            raise
        finally:
            self._verified.pop(manifest.job_id, None)
            self._discard_chunk_acks(manifest.job_id)

    def _on_verified(self, metadata, payload):
        event = self._verified.get(metadata.get("job_id"))
        if event is not None:
            event.set()

    def _on_paste_progress(self, metadata, payload):
        job_id = metadata.get("job_id")
        try:
            validate_transfer_id(job_id)
        except ValidationError:
            return False
        try:
            phase = TransferPhase(metadata.get("phase"))
        except (TypeError, ValueError):
            return False
        with self._paste_lock:
            job = self._paste_jobs.get(job_id)
        if job is None:
            if phase not in {TransferPhase.FAILED, TransferPhase.CANCELLED}:
                return False
            bytes_done = metadata.get("bytes_done")
            bytes_total = metadata.get("bytes_total")
            speed = metadata.get("bytes_per_second", 0.0)
            if (
                not isinstance(job_id, str)
                or not isinstance(bytes_done, int) or bytes_done < 0
                or not isinstance(bytes_total, int) or bytes_total < bytes_done
                or not isinstance(speed, (int, float)) or speed < 0
            ):
                return False
            with self._paste_lock:
                self._early_paste_terminal[job_id] = dict(metadata)
                self._early_paste_terminal.move_to_end(job_id)
                while len(self._early_paste_terminal) > self._early_paste_limit:
                    self._early_paste_terminal.popitem(last=False)
            return True
        label, expected_total = job
        try:
            bytes_done = metadata.get("bytes_done")
            bytes_total = metadata.get("bytes_total")
            speed = metadata.get("bytes_per_second", 0.0)
            if phase not in {
                TransferPhase.PASTING,
                TransferPhase.COMPLETED,
                TransferPhase.CANCELLED,
                TransferPhase.FAILED,
            }:
                return False
            if not isinstance(bytes_done, int) or not 0 <= bytes_done <= expected_total:
                return False
            if bytes_total != expected_total or not isinstance(speed, (int, float)) or speed < 0:
                return False
        except (TypeError, ValueError):
            return False
        current = self.controller.status(job_id) if self.controller else None
        if current is not None and bytes_done < current.bytes_done:
            return False
        if self.controller:
            self.controller.update(
                job_id, phase, label, bytes_done, bytes_total, speed,
                error_code=(
                    _safe_destination_error_code(metadata.get("error_code"))
                    if phase is TransferPhase.FAILED
                    else None
                ),
            )
        if phase in {
            TransferPhase.COMPLETED,
            TransferPhase.CANCELLED,
            TransferPhase.FAILED,
        }:
            with self._paste_lock:
                self._paste_jobs.pop(job_id, None)
        return True

    def _waiting(self, manifest, label):
        if self.controller:
            current = self.controller.status(manifest.job_id)
            if current is not None and current.phase in {
                TransferPhase.PASTING,
                TransferPhase.VERIFYING_RESULT,
                TransferPhase.CANCELLING,
            }:
                return
            self.controller.update(
                manifest.job_id, TransferPhase.WAITING_FOR_EXPLORER,
                label, 0, 0, 0.0,
            )

    def _send_chunk(self, job_id, relative_path, offset, chunk, compress):
        encoded = encode_chunk(chunk, compress)
        metadata = {
            "type": "chunk",
            "job_id": job_id,
            "relative_path": relative_path,
            "offset": offset,
            "compressed": encoded.compressed,
            "original_size": encoded.original_size,
        }
        if not getattr(self.lane, "supports_chunk_ack", False):
            self.lane.send(metadata, encoded.data)
            return offset + len(chunk)

        ack_key = (job_id, relative_path, offset)
        self._reserve_chunk_window(ack_key, len(chunk))
        try:
            self.lane.send(metadata, encoded.data)
        except Exception:
            self._release_chunk_ack(ack_key)
            raise
        return offset + len(chunk)

    def _reserve_chunk_window(self, ack_key, size):
        if size > self.max_in_flight_bytes:
            raise ValueError("file chunk exceeds the transfer window")
        deadline = self.clock() + 30
        with self._chunk_ack_condition:
            while self._in_flight_bytes + size > self.max_in_flight_bytes:
                self._check_cancelled(ack_key[0])
                if self.clock() >= deadline:
                    raise TimeoutError("destination did not acknowledge file data")
                self._chunk_ack_condition.wait(0.05)
            self._chunk_acks[ack_key] = size
            self._in_flight_bytes += size

    def _wait_for_chunk_acks(self, job_id, relative_path):
        if not getattr(self.lane, "supports_chunk_ack", False):
            return
        deadline = self.clock() + 30
        with self._chunk_ack_condition:
            while any(
                key[0] == job_id and key[1] == relative_path
                for key in self._chunk_acks
            ):
                self._check_cancelled(job_id)
                if self.clock() >= deadline:
                    raise TimeoutError("destination did not acknowledge file data")
                self._chunk_ack_condition.wait(0.05)

    def _release_chunk_ack(self, ack_key):
        with self._chunk_ack_condition:
            size = self._chunk_acks.pop(ack_key, None)
            if size is None:
                return False
            self._in_flight_bytes -= size
            self._chunk_ack_condition.notify_all()
        return True

    def _discard_chunk_acks(self, job_id):
        with self._chunk_ack_condition:
            keys = [key for key in self._chunk_acks if key[0] == job_id]
            for key in keys:
                self._in_flight_bytes -= self._chunk_acks.pop(key)
            if keys:
                self._chunk_ack_condition.notify_all()

    def _on_chunk_received(self, metadata, payload):
        job_id = metadata.get("job_id")
        relative_path = metadata.get("relative_path")
        offset = metadata.get("offset")
        try:
            validate_transfer_id(job_id)
        except ValidationError:
            return False
        if not isinstance(relative_path, str) or not relative_path:
            return False
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            return False
        return self._release_chunk_ack((job_id, relative_path, offset))

    def _check_cancelled(self, job_id):
        if self.controller:
            self.controller.check_cancelled(job_id)

def _manifest_label(manifest):
    files = [item for item in manifest.items if item.item_type is ItemType.FILE]
    directories = [item for item in manifest.items if item.item_type is ItemType.DIRECTORY]
    if len(files) == 1 and not directories:
        return files[0].relative_path.rsplit("/", 1)[-1]
    if directories:
        return f"{len(directories)} folder{'s' if len(directories) != 1 else ''} + {len(files)} files"
    return f"{len(files)} files"

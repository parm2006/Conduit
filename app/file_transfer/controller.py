import threading

from .status import TransferPhase, TransferStatus


class TransferCancelled(Exception):
    pass


class TransferController:
    def __init__(self):
        self._lock = threading.Lock()
        self._statuses = {}
        self._cancelled = set()
        self._subscribers = []

    def subscribe(self, callback):
        with self._lock:
            self._subscribers.append(callback)

    def update(
        self,
        job_id,
        phase,
        label,
        bytes_done,
        bytes_total,
        bytes_per_second=0.0,
        error_code=None,
    ):
        status = TransferStatus(
            job_id,
            phase,
            label,
            bytes_done,
            bytes_total,
            bytes_per_second,
            error_code,
        )
        with self._lock:
            previous = self._statuses.get(job_id)
            if previous is not None and previous.is_terminal:
                return previous
            if (
                job_id in self._cancelled
                and phase not in {TransferPhase.CANCELLING, TransferPhase.CANCELLED}
            ):
                return previous
            self._statuses[job_id] = status
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            callback(status)
        return status

    def cancel(self, job_id):
        with self._lock:
            previous = self._statuses.get(job_id)
            if previous is None or previous.is_terminal or job_id in self._cancelled:
                return False
            self._cancelled.add(job_id)
        self.update(
            job_id,
            TransferPhase.CANCELLING,
            previous.label,
            previous.bytes_done,
            previous.bytes_total,
            previous.bytes_per_second,
        )
        return True

    def confirm_cancelled(self, job_id):
        with self._lock:
            previous = self._statuses.get(job_id)
            if (
                previous is None
                or previous.is_terminal
                or job_id not in self._cancelled
            ):
                return False
        self.update(
            job_id,
            TransferPhase.CANCELLED,
            previous.label,
            previous.bytes_done,
            previous.bytes_total,
            previous.bytes_per_second,
        )
        return True

    def check_cancelled(self, job_id):
        with self._lock:
            cancelled = job_id in self._cancelled
        if cancelled:
            raise TransferCancelled(job_id)

    def status(self, job_id):
        with self._lock:
            return self._statuses.get(job_id)

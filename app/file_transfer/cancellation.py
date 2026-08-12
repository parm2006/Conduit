"""Idempotent, symmetric cancellation for the file-transfer lane."""

import threading
import uuid
from collections import OrderedDict

from .validation import ValidationError, validate_transfer_id


class TransferCancellation:
    def __init__(self, lane, controller, receiver, tombstone_limit=256):
        self.lane = lane
        self.controller = controller
        self.receiver = receiver
        self.tombstone_limit = int(tombstone_limit)
        self._outbound = {}
        self._handled = OrderedDict()
        self._finished = OrderedDict()
        self._lock = threading.Lock()
        lane.register_callback("cancel_job", self._on_cancel_job)
        lane.register_callback("cancel_ack", self._on_cancel_ack)
        lane.register_callback("disconnected", self._on_disconnected)

    def request(self, job_id):
        try:
            validate_transfer_id(job_id)
        except ValidationError:
            return False
        with self._lock:
            if job_id in self._outbound or job_id in self._finished:
                return False
        changed = self.controller.cancel(job_id)
        receiver_changed = self.receiver.cancel_job(job_id)
        if not changed and not receiver_changed:
            return False
        cancellation_id = uuid.uuid4().hex
        with self._lock:
            if job_id in self._outbound or job_id in self._finished:
                return False
            self._outbound[job_id] = cancellation_id
        self.lane.send({
            "type": "cancel_job", "job_id": job_id,
            "cancellation_id": cancellation_id,
        })
        return True

    def _on_cancel_job(self, metadata, payload):
        job_id = metadata.get("job_id")
        cancellation_id = metadata.get("cancellation_id")
        try:
            validate_transfer_id(job_id)
            validate_transfer_id(cancellation_id, "cancellation ID")
        except ValidationError:
            return False
        key = (job_id, cancellation_id)
        with self._lock:
            duplicate = key in self._handled
            self._handled[key] = None
            self._handled.move_to_end(key)
            while len(self._handled) > self.tombstone_limit:
                self._handled.popitem(last=False)
        if not duplicate:
            self.controller.cancel(job_id)
            self.receiver.cancel_job(job_id)
            self.controller.confirm_cancelled(job_id)
            with self._lock:
                self._remember_finished(job_id)
        self.lane.send({
            "type": "cancel_ack", "job_id": job_id,
            "cancellation_id": cancellation_id,
        })
        return not duplicate

    def _on_cancel_ack(self, metadata, payload):
        job_id = metadata.get("job_id")
        cancellation_id = metadata.get("cancellation_id")
        try:
            validate_transfer_id(job_id)
            validate_transfer_id(cancellation_id, "cancellation ID")
        except ValidationError:
            return False
        with self._lock:
            if self._outbound.get(job_id) != cancellation_id:
                return False
            self._outbound.pop(job_id, None)
            self._remember_finished(job_id)
        self.controller.confirm_cancelled(job_id)
        return True

    def _remember_finished(self, job_id):
        self._finished[job_id] = None
        self._finished.move_to_end(job_id)
        while len(self._finished) > self.tombstone_limit:
            self._finished.popitem(last=False)

    def _on_disconnected(self, metadata, payload):
        with self._lock:
            pending = tuple(self._outbound)
            self._outbound.clear()
            for job_id in pending:
                self._remember_finished(job_id)
        for job_id in pending:
            self.controller.confirm_cancelled(job_id)

"""Asynchronous source-side browser-window capture coordination."""

from dataclasses import dataclass
import logging
import secrets
import threading
import time

from .edge_band import window_in_activation_band
from .window_match import NativeWindowObservation, match_window


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingCapture:
    gesture_id: str
    request_id: str
    source_display_id: str
    source_side: str
    topology_version: int
    instance_id: str
    window_id: int
    bridge_epoch: str | None
    metadata_revision: int | None
    expires_at: float


class BrowserHandoffCoordinator:
    """Join a claimed native move to one exact extension window snapshot.

    The coordinator only schedules bridge work from the input callback. URL
    validation and network publication happen after the bridge replies.
    """

    def __init__(
        self,
        *,
        desktop,
        move_tracker,
        to_physical,
        send_candidate,
        now=time.monotonic,
        token_ttl_seconds=1.0,
        max_pending=8,
    ):
        self.desktop = desktop
        self.move_tracker = move_tracker
        self.to_physical = to_physical
        self.send_candidate = send_candidate
        self.now = now
        self.token_ttl_seconds = float(token_ttl_seconds)
        self.max_pending = int(max_pending)
        self._pending = {}
        self._lock = threading.RLock()
        previous = getattr(desktop, "on_snapshot", None)

        def on_snapshot(instance_id, message):
            if previous is not None:
                try:
                    previous(instance_id, message)
                except Exception:
                    pass
            self.handle_snapshot(instance_id, message)

        desktop.on_snapshot = on_snapshot

    def claim_edge(self, *, display_rect, edge_region, source_display_id, source_side, topology_version):
        logger.info(
            "browser_handoff stage=edge_claim display=%s side=%s topology=%s",
            source_display_id, source_side, topology_version,
        )
        if (
            type(topology_version) is not int or topology_version < 0
            or type(source_display_id) is not str or not source_display_id
            or source_side not in {"left", "right", "top", "bottom"}
        ):
            return None
        token = self.move_tracker.claim_active_move(now=self.now())
        if token is None:
            logger.info("browser_handoff stage=move_token_missing")
            return None
        if not window_in_activation_band(token.bounds, display_rect, edge_region):
            logger.info("browser_handoff stage=edge_band_rejected hwnd=%s", token.hwnd)
            return None
        observed_at = self.now()
        native = NativeWindowObservation(
            hwnd=token.hwnd,
            process_id=token.process_id,
            process_created=token.process_created,
            bounds=token.bounds,
            observed_at=observed_at,
        )
        candidates = self.desktop.browser_candidates(self.to_physical)
        matched = match_window(native, candidates, now=observed_at)
        if matched is None:
            logger.info("browser_handoff stage=window_match_rejected hwnd=%s candidates=%d", token.hwnd, len(candidates))
            return None
        with self._lock:
            now = self.now()
            for request_id, pending in tuple(self._pending.items()):
                if pending.expires_at <= now:
                    self._pending.pop(request_id, None)
            if len(self._pending) >= self.max_pending:
                logger.info("browser_handoff stage=pending_capacity_rejected")
                return None
            gesture_id = secrets.token_hex(16)
            request_id = secrets.token_hex(16)
            pending = PendingCapture(
                gesture_id=gesture_id,
                request_id=request_id,
                source_display_id=source_display_id,
                source_side=source_side,
                topology_version=topology_version,
                instance_id=matched.browser_instance_id,
                window_id=matched.window_id,
                bridge_epoch=matched.bridge_epoch,
                metadata_revision=matched.metadata_revision,
                expires_at=now + self.token_ttl_seconds,
            )
            self._pending[request_id] = pending
        logger.info(
            "browser_handoff stage=snapshot_requested gesture=%s instance=%s window=%s",
            gesture_id, matched.browser_instance_id, matched.window_id,
        )
        threading.Thread(
            target=self._request_snapshot,
            args=(pending,),
            name="browser-handoff-snapshot",
            daemon=True,
        ).start()
        return gesture_id

    def handle_snapshot(self, instance_id, message):
        if type(message) is not dict or type(instance_id) is not str:
            return False
        request_id = message.get("request_id")
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None:
                logger.info("browser_handoff stage=snapshot_rejected reason=unknown_request")
                return False
            if pending.expires_at < self.now():
                logger.info("browser_handoff stage=snapshot_rejected reason=expired")
                self._pending.pop(request_id, None)
                return False
            if instance_id != pending.instance_id:
                logger.info("browser_handoff stage=snapshot_rejected reason=instance_mismatch")
                return False
            if pending.bridge_epoch is not None and message.get("_bridge_epoch") != pending.bridge_epoch:
                logger.info("browser_handoff stage=snapshot_rejected reason=bridge_epoch_mismatch")
                self._pending.pop(request_id, None)
                return False
            snapshot = message.get("snapshot")
            if type(snapshot) is not dict:
                logger.info("browser_handoff stage=snapshot_rejected reason=missing_snapshot")
                self._pending.pop(request_id, None)
                return False
            if snapshot.get("window_id") != pending.window_id:
                logger.info("browser_handoff stage=snapshot_rejected reason=window_mismatch")
                self._pending.pop(request_id, None)
                return False
            if pending.metadata_revision is not None and snapshot.get("revision") != pending.metadata_revision:
                logger.info("browser_handoff stage=snapshot_rejected reason=revision_mismatch")
                self._pending.pop(request_id, None)
                return False
            if message.get("epoch") is not None and message.get("epoch") != pending.instance_id:
                logger.info("browser_handoff stage=snapshot_rejected reason=instance_epoch_mismatch")
                self._pending.pop(request_id, None)
                return False
            if snapshot.get("complete_capture") is not True:
                logger.info("browser_handoff stage=snapshot_rejected reason=incomplete")
                self._pending.pop(request_id, None)
                return False
            if snapshot.get("incognito") is True:
                logger.info("browser_handoff stage=snapshot_rejected reason=incognito_unsupported")
                self._pending.pop(request_id, None)
                return False
            candidate = {
                "protocol": 1,
                "gesture_id": pending.gesture_id,
                "source_display_id": pending.source_display_id,
                "source_side": pending.source_side,
                "topology_version": pending.topology_version,
                "incognito": snapshot.get("incognito") is True,
                "total_count": snapshot.get("total_count"),
                "entries": snapshot.get("entries"),
                "complete_capture": True,
            }
            self._pending.pop(request_id, None)
        logger.info(
            "browser_handoff stage=candidate_ready gesture=%s tabs=%s",
            candidate["gesture_id"], candidate["total_count"],
        )
        threading.Thread(
            target=self._publish_candidate,
            args=(candidate,),
            name="browser-handoff-candidate",
            daemon=True,
        ).start()
        return True

    def cancel(self):
        with self._lock:
            self._pending.clear()

    def _request_snapshot(self, pending):
        try:
            submitted = self.desktop.request_snapshot(
                pending.instance_id, pending.window_id, pending.request_id,
            )
            if not submitted:
                logger.info("browser_handoff stage=snapshot_request_failed instance=%s window=%s", pending.instance_id, pending.window_id)
                with self._lock:
                    self._pending.pop(pending.request_id, None)
        except Exception:
            logger.info("browser_handoff stage=snapshot_request_exception instance=%s window=%s", pending.instance_id, pending.window_id)
            with self._lock:
                self._pending.pop(pending.request_id, None)

    def _publish_candidate(self, candidate):
        try:
            sent = bool(self.send_candidate(candidate))
            logger.info(
                "browser_handoff stage=candidate_sent gesture=%s sent=%s",
                candidate.get("gesture_id"), sent,
            )
        except Exception:
            logger.info("browser_handoff stage=candidate_send_exception gesture=%s", candidate.get("gesture_id"))

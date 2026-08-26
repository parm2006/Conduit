"""Server-owned ordering and fan-out for the global clipboard."""

from dataclasses import dataclass
import logging
import threading

from app.clipboard_formats import ClipboardSnapshot
from app.latest_wins_sender import LatestWinsSender
from app.safe_errors import error_name


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClipboardHubItem:
    revision: int
    source_id: str
    source_sequence: int
    kind: str
    snapshot: ClipboardSnapshot | None = None


class _Endpoint:
    def __init__(self, endpoint_id, deliver, paused):
        self.endpoint_id = endpoint_id
        self.deliver = deliver
        self.sender = LatestWinsSender(self._deliver)
        if paused:
            self.sender.pause()

    def _deliver(self, work):
        return self.deliver(work["item"])

    def submit(self, item):
        return self.sender.submit({"item": item})

    def pause(self):
        return self.sender.pause()

    def resume(self):
        return self.sender.resume()

    def stop(self):
        self.sender.stop()


class ClipboardHub:
    """Assign cluster revisions and deliver only the newest pending item.

    Calls to ``accept_*`` are the serialized Server receive boundary. Client
    clocks are never compared. Each destination owns a latest-wins worker so
    capture and submission remain non-blocking while Apply holds delivery.
    """

    def __init__(self, server_id):
        if not isinstance(server_id, str) or not server_id:
            raise ValueError("clipboard hub server ID is invalid")
        self.server_id = server_id
        self._lock = threading.RLock()
        self._revision = 0
        self._latest_item = None
        self._source_sequences = {}
        self._source_domains = {}
        self._endpoints = {}
        self._delivery_paused = False
        self._stopped = False

    @property
    def revision(self):
        with self._lock:
            return self._revision

    @property
    def latest_item(self):
        with self._lock:
            return self._latest_item

    def register_endpoint(self, endpoint_id, deliver, *, source_domain=None):
        if not isinstance(endpoint_id, str) or not endpoint_id:
            raise ValueError("clipboard endpoint ID is invalid")
        if not callable(deliver):
            raise TypeError("clipboard endpoint delivery must be callable")
        previous = None
        with self._lock:
            if self._stopped:
                return False
            previous = self._endpoints.pop(endpoint_id, None)
            previous_domain = self._source_domains.pop(endpoint_id, None)
            if previous_domain is not None:
                self._source_sequences.pop(previous_domain, None)
            endpoint = _Endpoint(
                endpoint_id,
                deliver,
                self._delivery_paused,
            )
            self._endpoints[endpoint_id] = endpoint
            self._source_domains[endpoint_id] = (
                object() if source_domain is None else source_domain
            )
            latest = self._latest_item
            if latest is not None and latest.kind == "ordinary":
                endpoint.submit(latest)
        if previous is not None:
            previous.stop()
        return True

    def disconnect_endpoint(self, endpoint_id):
        with self._lock:
            endpoint = self._endpoints.pop(endpoint_id, None)
            source_domain = self._source_domains.pop(endpoint_id, None)
            if source_domain is not None:
                self._source_sequences.pop(source_domain, None)
        if endpoint is None:
            return False
        endpoint.stop()
        return True

    def accept_ordinary(
        self,
        source_id,
        source_sequence,
        snapshot,
        *,
        source_domain=None,
    ):
        if not isinstance(snapshot, ClipboardSnapshot):
            raise TypeError("ordinary clipboard item must contain a snapshot")
        return self._accept(
            source_id,
            source_sequence,
            "ordinary",
            snapshot,
            source_domain,
        )

    def accept_offer(
        self,
        source_id,
        source_sequence,
        kind,
        *,
        source_domain=None,
    ):
        if kind != "files":
            raise ValueError("clipboard offer kind is invalid")
        return self._accept(
            source_id,
            source_sequence,
            kind,
            None,
            source_domain,
        )

    def _accept(
        self,
        source_id,
        source_sequence,
        kind,
        snapshot,
        source_domain,
    ):
        if not isinstance(source_id, str) or not source_id:
            return None
        if type(source_sequence) is not int or source_sequence < 0:
            return None
        with self._lock:
            if self._stopped or source_id not in self._endpoints:
                return None
            active_domain = self._source_domains[source_id]
            if source_domain is not None and source_domain != active_domain:
                return None
            previous_sequence = self._source_sequences.get(active_domain, -1)
            if source_sequence <= previous_sequence:
                return None
            self._source_sequences[active_domain] = source_sequence
            self._revision += 1
            item = ClipboardHubItem(
                self._revision,
                source_id,
                source_sequence,
                kind,
                snapshot,
            )
            self._latest_item = item
            destinations = tuple(
                endpoint
                for endpoint_id, endpoint in self._endpoints.items()
                if endpoint_id != source_id
            )
            for endpoint in destinations:
                endpoint.submit(item)
        logger.info(
            "Clipboard hub accepted item (source=%s revision=%d kind=%s formats=%s bytes=%d)",
            source_id,
            item.revision,
            kind,
            "" if snapshot is None else ",".join(
                entry.kind for entry in snapshot.entries
            ),
            0 if snapshot is None else sum(
                len(entry.data) for entry in snapshot.entries
            ),
        )
        return item

    def pause_delivery(self):
        with self._lock:
            if self._stopped:
                return False
            self._delivery_paused = True
            endpoints = tuple(self._endpoints.values())
            for endpoint in endpoints:
                endpoint.pause()
            return True

    def resume_delivery(self):
        with self._lock:
            if self._stopped:
                return False
            self._delivery_paused = False
            endpoints = tuple(self._endpoints.values())
            for endpoint in endpoints:
                endpoint.resume()
            return True

    def stop(self):
        with self._lock:
            endpoints = tuple(self._endpoints.values())
            self._endpoints.clear()
            self._source_sequences.clear()
            self._source_domains.clear()
            self._latest_item = None
            self._revision = 0
            self._stopped = True
        for endpoint in endpoints:
            try:
                endpoint.stop()
            except Exception as error:
                logger.error(
                    "Clipboard endpoint stop failed (%s)",
                    error_name(error),
                )
        return True

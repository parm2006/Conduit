from dataclasses import dataclass


ENDPOINTS = frozenset(("server", "client"))
OFFER_KINDS = frozenset(("files", "ordinary"))


def should_transfer_files(kind, source, destination):
    return kind == "files" and source != destination


@dataclass(frozen=True)
class ClipboardOffer:
    session_id: str
    revision: int
    source: str
    kind: str
    sequence: int

    def to_message(self):
        return {
            "type": "clipboard_offer",
            "session_id": self.session_id,
            "revision": self.revision,
            "source": self.source,
            "kind": self.kind,
            "sequence": self.sequence,
        }

    @classmethod
    def from_message(cls, message):
        if not isinstance(message, dict):
            return None
        session_id = message.get("session_id")
        revision = message.get("revision")
        source = message.get("source")
        kind = message.get("kind")
        sequence = message.get("sequence")
        if (
            not isinstance(session_id, str)
            or not session_id
            or type(revision) is not int
            or revision < 1
            or source not in ENDPOINTS
            or kind not in OFFER_KINDS
            or type(sequence) is not int
            or sequence < 0
        ):
            return None
        return cls(session_id, revision, source, kind, sequence)


class ClipboardOfferState:
    def __init__(self, local_source):
        if local_source not in ENDPOINTS:
            raise ValueError("local clipboard offer source is invalid")
        self.local_source = local_source
        self.session_id = None
        self.local_revision = 0
        self.remote_revision = 0
        self.cluster_revision = 0
        self.current_offer = None

    def start_session(self, session_id):
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("clipboard offer session is invalid")
        self.session_id = session_id
        self.local_revision = 0
        self.remote_revision = 0
        self.cluster_revision = 0
        self.current_offer = None

    def observe_local(self, kind, sequence):
        if self.session_id is None:
            raise RuntimeError("clipboard offer session has not started")
        if kind not in OFFER_KINDS:
            raise ValueError("clipboard offer kind is invalid")
        if type(sequence) is not int or sequence < 0:
            raise ValueError("clipboard offer sequence is invalid")
        self.local_revision += 1
        offer = ClipboardOffer(
            self.session_id,
            self.local_revision,
            self.local_source,
            kind,
            sequence,
        )
        self.current_offer = offer
        return offer

    def accept_remote(self, message):
        offer = ClipboardOffer.from_message(message)
        if (
            offer is None
            or offer.session_id != self.session_id
            or offer.source == self.local_source
            or offer.revision <= self.remote_revision
        ):
            return False
        self.remote_revision = offer.revision
        self.current_offer = offer
        return True

    def accept_snapshot(self, message):
        offer = ClipboardOffer.from_message(message)
        if (
            offer is None
            or offer.session_id != self.session_id
            or offer.source == self.local_source
        ):
            return False
        if offer == self.current_offer:
            return True
        if (
            self.current_offer is not None
            and self.current_offer.source == self.local_source
        ):
            return False
        return self.accept_remote(message)

    def accept_cluster(self, revision, source, kind, sequence, session_id=None):
        """Install a Server-authoritative offer at an endpoint.

        ``source`` remains endpoint-relative for paste routing (local or remote),
        while ``revision`` is the global Server receive-order revision.
        """
        if self.session_id is None:
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError("clipboard offer session has not started")
            self.start_session(session_id)
        if (
            type(revision) is not int
            or revision < 1
            or source not in ENDPOINTS
            or kind not in OFFER_KINDS
            or type(sequence) is not int
            or sequence < 0
        ):
            return False
        if revision <= self.cluster_revision:
            return False
        offer = ClipboardOffer(
            self.session_id,
            revision,
            source,
            kind,
            sequence,
        )
        self.cluster_revision = revision
        self.current_offer = offer
        return True

    def should_transfer_to(self, destination):
        offer = self.current_offer
        return (
            destination in ENDPOINTS
            and offer is not None
            and should_transfer_files(offer.kind, offer.source, destination)
        )


class PasteCoordinator:
    CTRL_KEYS = frozenset(("ctrl", "ctrl_l", "ctrl_r"))

    def __init__(self, on_remote_file_paste):
        self.on_remote_file_paste = on_remote_file_paste
        self.transfer_required = False
        self.current_offer = None
        self.destination = None
        self.before_paste = None
        self._pressed_ctrl = set()
        self._suppressing_v = False

    def set_route(self, offer, destination):
        if offer is not None and not isinstance(offer, ClipboardOffer):
            raise TypeError("clipboard route offer is invalid")
        if destination not in ENDPOINTS:
            raise ValueError("clipboard route destination is invalid")
        self.current_offer = offer
        self.destination = destination
        self.transfer_required = (
            offer is not None
            and should_transfer_files(offer.kind, offer.source, destination)
        )
        return self.transfer_required

    def on_key_press(self, key):
        if key in self.CTRL_KEYS:
            self._pressed_ctrl.add(key)
            return False
        if key.lower() == "v" and self._pressed_ctrl:
            if not self._suppressing_v and self.before_paste is not None:
                if self.before_paste() is False:
                    self._suppressing_v = True
                    return True
            if not self.transfer_required:
                return False
            if not self._suppressing_v:
                self._suppressing_v = True
                self.on_remote_file_paste()
            return True
        return False

    def on_key_release(self, key):
        if key in self.CTRL_KEYS:
            self._pressed_ctrl.discard(key)
            return False
        if key.lower() == "v" and self._suppressing_v:
            self._suppressing_v = False
            return True
        return False

    def reset(self):
        self.transfer_required = False
        self.current_offer = None
        self.destination = None
        self._pressed_ctrl.clear()
        self._suppressing_v = False

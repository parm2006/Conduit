"""Pull-based monitor video over the existing paired TLS lanes.

Only one frame may be outstanding. Capture, JPEG work and socket sends never
run on Tk's thread. Requests are bound to an acknowledged cursor handoff.
"""
import base64
import binascii
from io import BytesIO
import logging
import threading
import time
import uuid

from PIL import Image

from app.input_router import RemoteClient

logger = logging.getLogger(__name__)
MAX_DIMENSION = 8192
MAX_PIXELS = 34_000_000
MAX_JPEG_BYTES = 12 * 1024 * 1024
FRAME_TIMEOUT = 1.0
FRAME_INTERVAL = 1 / 20


def fit_rect(viewport, source):
    width, height = viewport
    source_width, source_height = source
    if min(width, height, source_width, source_height) <= 0:
        raise ValueError('positive image dimensions required')
    scale = min(width / source_width, height / source_height)
    fitted_width = max(1, round(source_width * scale))
    fitted_height = max(1, round(source_height * scale))
    return ((width - fitted_width) // 2, (height - fitted_height) // 2,
            fitted_width, fitted_height)


def valid_size(width, height):
    return (type(width) is int and type(height) is int
            and 0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION
            and width * height <= MAX_PIXELS)


def capture_authorized(client, request):
    active = getattr(client, 'active_topology_config', None) or {}
    return bool(
        getattr(client, 'is_active', False)
        and isinstance(request.get('request_id'), str)
        and 0 < len(request['request_id']) <= 64
        and isinstance(request.get('handoff_id'), str) and request['handoff_id']
        and request.get('handoff_id') == active.get('handoff_id')
        and request.get('display_id') == active.get('destination_display_id')
        and type(request.get('topology_version')) is int
        and request['topology_version'] == active.get('topology_version')
        and valid_size(request.get('max_width'), request.get('max_height'))
    )


def decode_frame(frame):
    width, height = frame.get('width'), frame.get('height')
    encoded = frame.get('jpeg')
    if (not valid_size(width, height) or not isinstance(encoded, str)
            or len(encoded) > (MAX_JPEG_BYTES + 2) // 3 * 4):
        raise ValueError('invalid video frame bounds')
    try:
        payload = base64.b64decode(encoded, validate=True)
        if len(payload) > MAX_JPEG_BYTES:
            raise ValueError('video frame too large')
        with Image.open(BytesIO(payload)) as image:
            if image.format != 'JPEG' or image.size != (width, height):
                raise ValueError('video dimensions do not match')
            return image.convert('RGB')
    except (binascii.Error, OSError, Image.DecompressionBombError) as error:
        raise ValueError('invalid video image') from error


class ClientVideoCapture:
    def __init__(self, client, capture=None):
        self.client = client
        self.capture = capture
        self._lock = threading.Lock()
        self._busy = False
        self._pending = None
        self._generation = 0

    def cancel(self):
        with self._lock:
            self._generation += 1
            self._pending = None

    def request(self, request):
        client = self.client
        if not capture_authorized(client, request):
            return False
        lane = getattr(client, 'data_network', None)
        if lane is None or not lane.authenticated:
            return False
        with self._lock:
            job = (dict(request), lane, self._generation)
            if self._busy:
                # A new monitor may be acknowledged while the preceding
                # capture is still running. Keep only the newest request.
                self._pending = job
                return True
            self._busy = True
        threading.Thread(target=self._drain, args=(job,),
                         name='remote-capture', daemon=True).start()
        return True

    def _drain(self, job):
        while job is not None:
            self._produce(*job)
            with self._lock:
                job, self._pending = self._pending, None
                if job is None:
                    self._busy = False

    def _produce(self, request, lane, generation):
        try:
            if generation != self._generation or not capture_authorized(self.client, request):
                return
            # The rectangle comes from local Windows discovery, never the wire.
            group = self.client.display_group
            display = group.display(request['display_id'])
            rect = display.rect
            if not display.enabled or not valid_size(rect.right - rect.left, rect.bottom - rect.top):
                return
            capture = self.capture
            if capture is None:
                from app.windows_capture import capture_monitor
                capture = capture_monitor
            image, cursor = capture(rect)
            _, _, width, height = fit_rect(
                (request['max_width'], request['max_height']), image.size)
            # Never upscale on the wire. Viewer upscales when appropriate.
            if width < image.width or height < image.height:
                image = image.resize((width, height), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, 'JPEG', quality=95, subsampling=0)
            payload = output.getvalue()
            if len(payload) > MAX_JPEG_BYTES:
                return
            if (generation != self._generation or self.client.data_network is not lane
                    or not capture_authorized(self.client, request)):
                return
            lane.send_message({
                'type': 'remote_video_frame', 'request_id': request['request_id'],
                'handoff_id': request['handoff_id'], 'display_id': request['display_id'],
                'width': image.width, 'height': image.height,
                'cursor': list(cursor),
                'jpeg': base64.b64encode(payload).decode('ascii'),
            })
        except Exception as error:
            logger.warning('Remote capture failed (%s)', type(error).__name__)


class ServerVideoReceiver:
    def __init__(self, server, viewport):
        self.server = server
        self.viewport = viewport
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._expected = None
        self._incoming = None
        self._latest = None
        self.selection = None
        self.last_frame_at = 0.0
        self.started_at = 0.0
        self.failed_selection = None
        server.data_network.register_callback('remote_video_frame', self.receive)
        self._thread = threading.Thread(target=self._run, name='remote-video', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        with self._lock:
            self._expected = self._incoming = self._latest = None

    def current_selection(self):
        router = getattr(self.server, 'input_router', None)
        if router is None or getattr(self.server, 'routing_suspended', False):
            return None
        state = router.state
        return state if isinstance(state, RemoteClient) else None

    def receive(self, frame):
        with self._lock:
            expected = self._expected
            if expected is None or self._stop.is_set():
                return False
            request_id, selection, _sent_at = expected
            if (frame.get('session_id') != selection.session_id
                    or frame.get('peer_identity') != selection.machine_id
                    or frame.get('request_id') != request_id
                    or frame.get('handoff_id') != selection.handoff_id
                    or frame.get('display_id') != selection.display_id
                    or self.current_selection() != selection):
                return False
            # Network callback stores at most one response; decoding is off-lane.
            self._incoming = frame
            self._wake.set()
            return True

    def take_frame(self):
        with self._lock:
            result, self._latest = self._latest, None
            return result

    def stalled(self, now=None):
        now = time.monotonic() if now is None else now
        selection = self.current_selection()
        return (selection is not None and selection == self.selection
                and (selection == self.failed_selection
                     or now - max(self.last_frame_at, self.started_at) >= FRAME_TIMEOUT))

    def _run(self):
        while not self._stop.is_set():
            selection = self.current_selection()
            if selection != self.selection:
                self.selection = selection
                self.started_at = time.monotonic()
                self.last_frame_at = 0.0
                with self._lock:
                    self._latest = self._incoming = self._expected = None
            if selection is None or selection == self.failed_selection:
                self._stop.wait(0.02)
                continue
            request_id = uuid.uuid4().hex
            started = time.monotonic()
            with self._lock:
                self._expected = (request_id, selection, started)
                self._incoming = None
                self._wake.clear()
            try:
                sent = self.server.control_network.send_message({
                    'type': 'remote_video_request', 'request_id': request_id,
                    'display_id': selection.display_id, 'handoff_id': selection.handoff_id,
                    'topology_version': self.server.input_router.topology.version,
                    'max_width': self.viewport[0], 'max_height': self.viewport[1],
                }, session_id=selection.session_id)
                if not sent:
                    raise ValueError('video request failed')
                # Wake frequently on ownership changes without waiting a full second.
                while (not self._stop.is_set() and self.current_selection() == selection
                       and time.monotonic() - started < FRAME_TIMEOUT):
                    if self._wake.wait(0.02):
                        break
                with self._lock:
                    frame = self._incoming
                    self._expected = self._incoming = None
                if self._stop.is_set() or self.current_selection() != selection:
                    continue
                if frame is None or time.monotonic() - started >= FRAME_TIMEOUT:
                    raise ValueError('video frame timed out')
                image = decode_frame(frame)
                x, y, width, height = fit_rect(self.viewport, image.size)
                if image.size != (width, height):
                    image = image.resize((width, height), Image.Resampling.LANCZOS)
                if self.current_selection() == selection and not self._stop.is_set():
                    with self._lock:
                        self._latest = (selection, image, (x, y), frame.get('cursor'))
                        self.last_frame_at = time.monotonic()
            except Exception as error:
                logger.warning('Remote video unavailable (%s)', type(error).__name__)
                self.failed_selection = selection
            self._stop.wait(max(0, FRAME_INTERVAL - (time.monotonic() - started)))

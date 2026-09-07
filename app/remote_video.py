"""Pull-based monitor video over the existing paired TLS lanes.

Only one frame may be outstanding. Capture, JPEG work and socket sends never
run on Tk's thread. Requests are bound to an acknowledged cursor handoff.
"""
import base64
import binascii
from io import BytesIO
import logging
import secrets
import threading
import time
import uuid

from PIL import Image

from app.input_router import RemoteClient
from app.ports import DEFAULT_STREAM_PORT

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
    def __init__(self, server, viewport, viewport_hwnd=None, stream_port=None):
        self.server = server
        self.viewport = viewport
        self.viewport_hwnd = viewport_hwnd
        self.stream_port = stream_port or DEFAULT_STREAM_PORT
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

        # Native streaming state
        self._native_receiver = None
        self._native_active = False
        self._native_stream_id = None
        self._native_failed_selection = None
        self._native_started_event = threading.Event()
        self._native_started_success = False

        server.data_network.register_callback('remote_video_frame', self.receive)
        server.control_network.register_callback('stream_started', self._on_stream_started)
        self._thread = threading.Thread(target=self._run, name='remote-video', daemon=True)
        self._thread.start()

    def _on_stream_started(self, message):
        if (self._native_stream_id is not None
                and message.get('stream_id') == self._native_stream_id):
            self._native_started_success = bool(message.get('success'))
            self._native_started_event.set()

    def is_native_active(self):
        return bool(getattr(self, '_native_active', False))

    def resize(self, width, height):
        self.viewport = (width, height)
        receiver = getattr(self, '_native_receiver', None)
        if receiver is not None:
            try:
                receiver.resize(width, height)
            except Exception as error:
                logger.debug("Native receiver resize failed: %s", error)

    def _start_native(self, selection):
        from app.native_streamer import (
            is_native_streaming_supported,
            NativeStreamerReceiver,
            STREAMER_EVENT_FIRST_FRAME,
            STREAMER_EVENT_NEED_KEYFRAME,
            STREAMER_EVENT_ERROR,
        )
        if not self.viewport_hwnd or not is_native_streaming_supported():
            return False
        try:
            self._stop_native()
            self._native_stream_id = uuid.uuid4().hex
            self._native_started_event.clear()
            self._native_started_success = False

            # Option A: Direct 256-bit CSPRNG key delivered over TLS Control Lane
            stream_key = secrets.token_bytes(32)

            # Resolve server IP on the network interface used by this client
            server_ip = None
            if hasattr(self.server.control_network, 'connection'):
                conn = self.server.control_network.connection(selection.session_id)
                if conn and getattr(conn, 'sock', None):
                    try:
                        server_ip = conn.sock.getsockname()[0]
                    except Exception:
                        pass
            if not server_ip or server_ip in ('0.0.0.0', ''):
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(('8.8.8.8', 80))
                    server_ip = s.getsockname()[0]
                    s.close()
                except Exception:
                    server_ip = '127.0.0.1'

            def on_native_event(code, msg):
                if code == STREAMER_EVENT_FIRST_FRAME:
                    self.last_frame_at = time.monotonic()
                    self._native_active = True
                elif code == STREAMER_EVENT_NEED_KEYFRAME:
                    try:
                        self.server.control_network.send_message({
                            'type': 'stream_keyframe_request',
                            'session_id': selection.session_id,
                            'stream_id': self._native_stream_id,
                        }, session_id=selection.session_id)
                    except Exception:
                        pass
                elif code == STREAMER_EVENT_ERROR:
                    logger.warning("Native receiver error (%s), falling back to GDI/JPEG", msg)
                    self._native_failed_selection = selection
                    self._native_active = False

            self._native_receiver = NativeStreamerReceiver(self.viewport_hwnd, on_event=on_native_event)
            started = self._native_receiver.start(self.stream_port, stream_key)
            if not started:
                self._stop_native()
                return False

            sent = self.server.control_network.send_message({
                'type': 'stream_start',
                'session_id': selection.session_id,
                'stream_id': self._native_stream_id,
                'server_ip': server_ip,
                'udp_port': self.stream_port,
                'display_id': selection.display_id,
                'stream_key': base64.b64encode(stream_key).decode('ascii'),
                'fps': 60,
                'bitrate_kbps': 10000,
            }, session_id=selection.session_id)
            if not sent:
                self._stop_native()
                return False

            # Wait up to 500ms for client's stream_started acknowledgment
            if self._native_started_event.wait(0.5) and self._native_started_success:
                self._native_active = True
                self.last_frame_at = time.monotonic()
                logger.info("Native hardware video streaming active for %s", selection.display_id)
                return True
            else:
                logger.info("Client did not start native stream, falling back to GDI/JPEG")
                self._stop_native()
                return False
        except Exception as exc:
            logger.warning("Failed to start native receiver (%s), falling back to GDI/JPEG", exc)
            self._stop_native()
            return False

    def _stop_native(self, session_id=None):
        if self._native_stream_id is not None:
            sid = session_id or (self.selection.session_id if self.selection else None)
            if sid:
                try:
                    self.server.control_network.send_message({
                        'type': 'stream_stop',
                        'session_id': sid,
                        'stream_id': self._native_stream_id,
                    }, session_id=sid)
                except Exception:
                    pass
            self._native_stream_id = None
        self._native_active = False
        if self._native_receiver is not None:
            try:
                self._native_receiver.stop()
                self._native_receiver.destroy()
            except Exception:
                pass
            self._native_receiver = None

    def stop(self):
        self._stop.set()
        self._wake.set()
        self._stop_native()
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
        if selection is None or selection != self.selection:
            return False
        elapsed = now - self.last_frame_at if self.last_frame_at > 0.0 else now - self.started_at
        timeout = FRAME_TIMEOUT if self.last_frame_at > 0.0 else 2.5
        if getattr(self, '_native_active', False):
            return elapsed >= timeout
        if selection == self.failed_selection:
            return True
        return elapsed >= timeout

    def _run(self):
        while not self._stop.is_set():
            selection = self.current_selection()
            if selection != self.selection:
                if self.selection is not None:
                    self._stop_native(self.selection.session_id)
                self.selection = selection
                self.started_at = time.monotonic()
                self.last_frame_at = 0.0
                with self._lock:
                    self._latest = self._incoming = self._expected = None

            if selection is None or selection == self.failed_selection:
                self._stop_native()
                self._stop.wait(0.02)
                continue

            # Try native streaming if supported and not previously failed for this selection
            if (self.viewport_hwnd is not None
                    and selection != self._native_failed_selection
                    and not self._native_active):
                if self._start_native(selection):
                    self.started_at = time.monotonic()
                else:
                    self._native_failed_selection = selection

            # While native streaming is active, monitor its liveness without pulling JPEG frames
            if self._native_active:
                while (not self._stop.is_set()
                       and self.current_selection() == selection
                       and self._native_active):
                    elapsed = (time.monotonic() - self.last_frame_at
                               if self.last_frame_at > 0.0
                               else time.monotonic() - self.started_at)
                    timeout = FRAME_TIMEOUT if self.last_frame_at > 0.0 else 2.5
                    if elapsed >= timeout:
                        logger.warning("Native stream stalled, falling back to GDI/JPEG")
                        self._stop_native()
                        self._native_failed_selection = selection
                        self.started_at = time.monotonic()
                        self.last_frame_at = 0.0
                        break
                    self._stop.wait(0.02)
                if self._stop.is_set() or self.current_selection() != selection:
                    continue

            # Fallback path: pull-based GDI/JPEG frame loop
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


"""Optional authenticated latest-position lane; TLS retains all input barriers.

A fresh TLS-delivered key is used for each connection. UDP is used only while
fresh authenticated probe acknowledgements confirm the path. No keys/buttons
are ever sent unreliably.
"""
import hmac
import secrets
import socket
import struct
import threading
import time
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_HEADER = struct.Struct('!QQ')
_POSITION = struct.Struct('!ii')
_INPUT = frozenset(('mouse_click', 'mouse_scroll', 'key_press', 'key_release'))
_BARRIERS = _INPUT | frozenset(('switch', 'release_all', 'mouse_move', 'mouse_move_batch'))

class MouseSender:
    def __init__(self, host, port, key):
        self.key = key
        self.cipher = AESGCM(key)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.connect((host, port))
        self.sock.settimeout(.1)
        self.sequence = 0
        self.barrier = 0
        self.position = None
        self.last_packet = None
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.confirmed_at = 0.
        self.thread = None

    @property
    def ready(self):
        return not self.stopped.is_set() and time.monotonic() - self.confirmed_at < .3

    def start(self):
        self.thread = threading.Thread(target=self._probe_loop, daemon=True, name='mouse-probe')
        self.thread.start()

    def _probe_loop(self):
        while not self.stopped.is_set():
            # Fresh challenges prevent recorded acknowledgements keeping a dead path alive.
            probe = b'P' + secrets.token_bytes(16)
            ack = b'A' + hmac.digest(self.key, probe, 'sha256')
            try:
                self.sock.send(probe)
                deadline = time.monotonic() + .1
                while time.monotonic() < deadline:
                    if hmac.compare_digest(self.sock.recv(128), ack):
                        self.confirmed_at = time.monotonic()
                        with self.lock:
                            if self.last_packet is not None:
                                self.sock.send(self.last_packet)
                        break
            except OSError:
                pass
            self.stopped.wait(.05)

    def packet(self, x, y):
        with self.lock:
            self.sequence += 1
            self.position = (int(x), int(y))
            header = _HEADER.pack(self.sequence, self.barrier)
            return header + self.cipher.encrypt(
                b'\0\0\0\0' + header[:8], _POSITION.pack(*self.position), header)

    def send_position(self, x, y):
        if not self.ready:
            return False
        previous = self.position
        try:
            packet = self.packet(x, y)
            self.sock.send(packet)
            with self.lock:
                self.last_packet = packet
            return True
        except OSError:
            self.position = previous
            self.confirmed_at = 0.
            return False

    def reliable(self, message):
        with self.lock:
            result = dict(message)
            kind = message.get('type')
            if kind == 'mouse_position':
                self.barrier += 1
                self.sequence += 1
                self.position = (int(message['x']), int(message['y']))
            elif kind in _BARRIERS:
                self.barrier += 1
                if self.position is not None and kind in _INPUT | {'mouse_move', 'mouse_move_batch'}:
                    result['_mouse_position'] = list(self.position)
                if kind not in _INPUT:
                    self.position = None
            else:
                return result
            self.last_packet = None
            result['_mouse_barrier'] = self.barrier
            result['_mouse_sequence'] = self.sequence
            return result

    def close(self):
        self.stopped.set()
        self.sock.close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(1)

class MouseReceiver:
    def __init__(self, host, port, callback):
        self.key = secrets.token_bytes(32)
        self.cipher = AESGCM(self.key)
        self.callback = callback
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((host, port))
        except BaseException:
            self.sock.close()
            raise
        self.port = self.sock.getsockname()[1]
        self.sock.settimeout(.1)
        self.barrier = 0
        self.sequence = 0
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.thread = None
        self.peer_ip = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name='mouse-receive')
        self.thread.start()

    def _run(self):
        while not self.stopped.is_set():
            try:
                packet, peer = self.sock.recvfrom(128)
                if self.peer_ip and peer[0] != self.peer_ip:
                    continue
                if len(packet) == 17 and packet[:1] == b'P':
                    # Only reply to the TLS peer; response cannot inject input.
                    self.sock.sendto(b'A' + hmac.digest(self.key, packet, 'sha256'), peer)
                else:
                    self.accept(packet)
            except OSError:
                continue

    def accept(self, packet):
        if len(packet) != _HEADER.size + _POSITION.size + 16:
            return False
        header = packet[:_HEADER.size]
        try:
            position = self.cipher.decrypt(b'\0\0\0\0' + header[:8], packet[_HEADER.size:], header)
        except InvalidTag:
            return False
        sequence, barrier = _HEADER.unpack(header)
        x, y = _POSITION.unpack(position)
        with self.lock:
            if self.stopped.is_set() or barrier != self.barrier or sequence <= self.sequence:
                return False
            self.sequence = sequence
            self.callback({'type': 'mouse_position', 'x': x, 'y': y})
            return True

    def dispatch(self, message, callback):
        with self.lock:
            barrier = message.get('_mouse_barrier')
            sequence = message.get('_mouse_sequence')
            if isinstance(barrier, int) and isinstance(sequence, int):
                self.barrier = max(self.barrier, barrier)
                self.sequence = max(self.sequence, sequence)
                position = message.get('_mouse_position')
                if (message.get('type') in _INPUT | {'mouse_move', 'mouse_move_batch'}
                        and isinstance(position, list) and len(position) == 2):
                    self.callback({'type': 'mouse_position', 'x': position[0], 'y': position[1]})
            callback(message)

    def close(self):
        self.stopped.set()
        self.sock.close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(1)

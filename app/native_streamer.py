"""Python ctypes bridge for conduit_streamer.dll.

Exposes high-performance native DXGI capture, hardware H.264 encoding/decoding,
and secure UDP streaming with zero-copy Direct3D 11 presentation.
"""
import ctypes
from ctypes import wintypes
import logging
import os
import sys

logger = logging.getLogger(__name__)

STREAMER_EVENT_STARTED = 1
STREAMER_EVENT_STOPPED = 2
STREAMER_EVENT_FIRST_FRAME = 3
STREAMER_EVENT_NEED_KEYFRAME = 4
STREAMER_EVENT_ERROR = -1

STREAMER_EVENT_CALLBACK = ctypes.WINFUNCTYPE(
    None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p
)

_DLL = None
_HARDWARE_SUPPORTED = None


def get_streamer_dll():
    global _DLL, _HARDWARE_SUPPORTED
    if _DLL is not None:
        return _DLL

    dll_path = os.path.join(os.path.dirname(__file__), "bin", "conduit_streamer.dll")
    if not os.path.exists(dll_path):
        logger.debug("conduit_streamer.dll not found at %s", dll_path)
        return None

    try:
        dll = ctypes.CDLL(dll_path)

        # Function signatures
        dll.streamer_get_version.restype = ctypes.c_int
        dll.streamer_get_version.argtypes = []

        dll.streamer_check_hardware_support.restype = ctypes.c_int
        dll.streamer_check_hardware_support.argtypes = [
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]

        # Sender API
        dll.streamer_sender_create.restype = ctypes.c_void_p
        dll.streamer_sender_create.argtypes = [STREAMER_EVENT_CALLBACK, ctypes.c_void_p]

        dll.streamer_sender_start.restype = ctypes.c_int
        dll.streamer_sender_start.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]

        dll.streamer_sender_request_keyframe.restype = ctypes.c_int
        dll.streamer_sender_request_keyframe.argtypes = [ctypes.c_void_p]

        dll.streamer_sender_stop.restype = ctypes.c_int
        dll.streamer_sender_stop.argtypes = [ctypes.c_void_p]

        dll.streamer_sender_destroy.restype = None
        dll.streamer_sender_destroy.argtypes = [ctypes.c_void_p]

        # Receiver API
        dll.streamer_receiver_create.restype = ctypes.c_void_p
        dll.streamer_receiver_create.argtypes = [
            wintypes.HWND,
            STREAMER_EVENT_CALLBACK,
            ctypes.c_void_p,
        ]

        dll.streamer_receiver_start.restype = ctypes.c_int
        dll.streamer_receiver_start.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
        ]

        dll.streamer_receiver_resize.restype = ctypes.c_int
        dll.streamer_receiver_resize.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ]

        dll.streamer_receiver_stop.restype = ctypes.c_int
        dll.streamer_receiver_stop.argtypes = [ctypes.c_void_p]

        dll.streamer_receiver_destroy.restype = None
        dll.streamer_receiver_destroy.argtypes = [ctypes.c_void_p]

        _DLL = dll
        return _DLL
    except Exception as error:
        logger.warning("Failed to load conduit_streamer.dll: %s", error)
        return None


def is_native_streaming_supported():
    global _HARDWARE_SUPPORTED
    if _HARDWARE_SUPPORTED is not None:
        return _HARDWARE_SUPPORTED

    dll = get_streamer_dll()
    if dll is None:
        _HARDWARE_SUPPORTED = False
        return False

    dxgi, enc, dec = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    dll.streamer_check_hardware_support(
        ctypes.byref(dxgi), ctypes.byref(enc), ctypes.byref(dec)
    )

    _HARDWARE_SUPPORTED = bool(dxgi.value and enc.value and dec.value)
    return _HARDWARE_SUPPORTED


class NativeStreamerSender:
    def __init__(self, on_event=None):
        self._dll = get_streamer_dll()
        if not self._dll:
            raise RuntimeError("conduit_streamer.dll is not available")
        self._on_event = on_event
        self._cb_ref = STREAMER_EVENT_CALLBACK(self._callback_handler)
        self._handle = self._dll.streamer_sender_create(self._cb_ref, None)
        if not self._handle:
            raise RuntimeError("streamer_sender_create failed")

    def _callback_handler(self, event_code, message, user_data):
        msg_str = message.decode("utf-8", "ignore") if message else ""
        if self._on_event:
            try:
                self._on_event(event_code, msg_str)
            except Exception:
                pass

    def start(self, display_index, target_ip, target_port, session_key, bitrate_kbps=10000, fps=60):
        if not isinstance(session_key, bytes) or len(session_key) != 32:
            raise ValueError("session_key must be exactly 32 bytes")
        rc = self._dll.streamer_sender_start(
            self._handle,
            int(display_index),
            target_ip.encode("ascii"),
            int(target_port),
            session_key,
            len(session_key),
            int(bitrate_kbps),
            int(fps),
        )
        return rc == 0

    def request_keyframe(self):
        return self._dll.streamer_sender_request_keyframe(self._handle) == 0

    def stop(self):
        if self._handle:
            self._dll.streamer_sender_stop(self._handle)

    def destroy(self):
        if self._handle:
            self._dll.streamer_sender_destroy(self._handle)
            self._handle = None

    def __del__(self):
        self.destroy()


class NativeStreamerReceiver:
    def __init__(self, hwnd, on_event=None):
        self._dll = get_streamer_dll()
        if not self._dll:
            raise RuntimeError("conduit_streamer.dll is not available")
        self._on_event = on_event
        self._cb_ref = STREAMER_EVENT_CALLBACK(self._callback_handler)
        self._handle = self._dll.streamer_receiver_create(hwnd, self._cb_ref, None)
        if not self._handle:
            raise RuntimeError("streamer_receiver_create failed")

    def _callback_handler(self, event_code, message, user_data):
        msg_str = message.decode("utf-8", "ignore") if message else ""
        if self._on_event:
            try:
                self._on_event(event_code, msg_str)
            except Exception:
                pass

    def start(self, listen_port, session_key):
        if not isinstance(session_key, bytes) or len(session_key) != 32:
            raise ValueError("session_key must be exactly 32 bytes")
        rc = self._dll.streamer_receiver_start(
            self._handle,
            int(listen_port),
            session_key,
            len(session_key),
        )
        return rc == 0

    def resize(self, width, height):
        return self._dll.streamer_receiver_resize(self._handle, int(width), int(height)) == 0

    def stop(self):
        if self._handle:
            self._dll.streamer_receiver_stop(self._handle)

    def destroy(self):
        if self._handle:
            self._dll.streamer_receiver_destroy(self._handle)
            self._handle = None

    def __del__(self):
        self.destroy()

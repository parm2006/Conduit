"""Small Windows DPAPI boundary used for Conduit local secrets."""

import ctypes
import os
from ctypes import wintypes


class DataProtectionError(OSError):
    pass


if os.name == "nt":
    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]


    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


def _input_blob(value):
    raw = bytes(value)
    buffer = ctypes.create_string_buffer(raw)
    blob = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def _copy_output(blob):
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        if blob.pbData:
            _kernel32.LocalFree(blob.pbData)


class WindowsDataProtector:
    """Protect bytes for the current Windows user without displaying UI."""

    _UI_FORBIDDEN = 0x1

    def protect(self, value):
        if os.name != "nt":
            raise DataProtectionError("Windows DPAPI is unavailable")
        source, keepalive = _input_blob(value)
        destination = _DataBlob()
        if not _crypt32.CryptProtectData(
            ctypes.byref(source),
            "Conduit local secret",
            None,
            None,
            None,
            self._UI_FORBIDDEN,
            ctypes.byref(destination),
        ):
            raise DataProtectionError(ctypes.get_last_error(), "DPAPI protection failed")
        del keepalive
        return _copy_output(destination)

    def unprotect(self, value):
        if os.name != "nt":
            raise DataProtectionError("Windows DPAPI is unavailable")
        source, keepalive = _input_blob(value)
        destination = _DataBlob()
        description = wintypes.LPWSTR()
        try:
            if not _crypt32.CryptUnprotectData(
                ctypes.byref(source),
                ctypes.byref(description),
                None,
                None,
                None,
                self._UI_FORBIDDEN,
                ctypes.byref(destination),
            ):
                raise DataProtectionError(ctypes.get_last_error(), "DPAPI unprotection failed")
            del keepalive
            return _copy_output(destination)
        finally:
            if description:
                _kernel32.LocalFree(description)


_default = WindowsDataProtector()


def protect(value):
    return _default.protect(value)


def unprotect(value):
    return _default.unprotect(value)

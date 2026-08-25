import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.display_topology import NativeRect
from app.windows_displays import (
    DisplayDiscoveryError,
    NativeDisplayRecord,
    WindowsDisplayDiscovery,
    _Win32DisplayBackend,
    display_group_from_message,
    display_group_to_message,
)


class FakeDisplayBackend:
    def __init__(self, records=(), error=None):
        self.records = records
        self.error = error

    def snapshot(self):
        if self.error is not None:
            raise self.error
        return self.records


class WindowsDisplayDiscoveryTests(unittest.TestCase):
    def test_translates_native_records_without_inventing_peer_displays(self):
        backend = FakeDisplayBackend(
            records=(
                NativeDisplayRecord(
                    stable_id="monitor-instance-primary",
                    rect=(-1920, 0, 0, 1080),
                    work_rect=(-1920, 0, 0, 1040),
                    dpi=120,
                    orientation_code=0,
                    primary=True,
                    enabled=True,
                ),
                NativeDisplayRecord(
                    stable_id="monitor-instance-portrait",
                    rect=(0, 0, 1440, 2560),
                    work_rect=(0, 0, 1440, 2520),
                    dpi=144,
                    orientation_code=1,
                    primary=False,
                    enabled=True,
                ),
            )
        )
        discovery = WindowsDisplayDiscovery(backend=backend)

        group = discovery.discover(machine_id="trusted-server", windows_name="ParthPC")

        self.assertEqual(group.machine_id, "trusted-server")
        self.assertEqual(group.windows_name, "ParthPC")
        self.assertEqual(len(group.displays), 2)
        primary, portrait = group.displays
        self.assertEqual(primary.display_id, "monitor-instance-primary")
        self.assertEqual(primary.rect, NativeRect(-1920, 0, 0, 1080))
        self.assertEqual(primary.work_rect, NativeRect(-1920, 0, 0, 1040))
        self.assertEqual(primary.dpi_percent, 125)
        self.assertTrue(primary.primary)
        self.assertEqual(portrait.orientation, 90)
        self.assertEqual(portrait.dpi_percent, 150)

    def test_native_failures_become_safe_discovery_errors(self):
        discovery = WindowsDisplayDiscovery(
            backend=FakeDisplayBackend(error=OSError("private device path"))
        )

        with self.assertRaisesRegex(DisplayDiscoveryError, "display discovery failed") as caught:
            discovery.discover(machine_id="server", windows_name="ParthPC")

        self.assertNotIn("private device path", str(caught.exception))

    def test_production_backend_uses_the_documented_interface_name_flag(self):
        class FakeWin32Api:
            @staticmethod
            def EnumDisplayMonitors():
                return ((0, None, None),)

            @staticmethod
            def GetMonitorInfo(_handle):
                return {
                    "Device": r"\\.\DISPLAY1",
                    "Monitor": (0, 0, 1920, 1080),
                    "Work": (0, 0, 1920, 1040),
                    "Flags": 1,
                }

            @staticmethod
            def EnumDisplayDevices(_name, _index, flags):
                if flags != 0x00000001:
                    raise AssertionError("wrong EnumDisplayDevices flag")
                return SimpleNamespace(DeviceID="stable-interface", DeviceKey="")

            @staticmethod
            def EnumDisplaySettings(_name, _mode):
                return SimpleNamespace(DisplayOrientation=0)

        fake_con = SimpleNamespace(ENUM_CURRENT_SETTINGS=-1, MONITORINFOF_PRIMARY=1)
        with patch.dict(
            "sys.modules",
            {"win32api": FakeWin32Api, "win32con": fake_con},
        ):
            records = _Win32DisplayBackend().snapshot()

        self.assertEqual(records[0].stable_id, "stable-interface")

    def test_dpi_lookup_converts_pywin32_handle_for_ctypes(self):
        class FakeHandle:
            def __int__(self):
                return 1234

        class FakeShcore:
            @staticmethod
            def GetDpiForMonitor(handle, _kind, dpi_x, dpi_y):
                if not isinstance(handle, ctypes.c_void_p):
                    raise ctypes.ArgumentError("expected native handle")
                dpi_x._obj.value = 144
                dpi_y._obj.value = 144
                return 0

        class FakeCtypes:
            c_uint = ctypes.c_uint
            c_void_p = ctypes.c_void_p
            byref = ctypes.byref

            @staticmethod
            def WinDLL(_name, use_last_error=True):
                return FakeShcore()

        dpi = _Win32DisplayBackend._effective_dpi(FakeCtypes, FakeHandle())

        self.assertEqual(dpi, 144)

    def test_display_inventory_round_trips_through_the_peer_message_boundary(self):
        group = WindowsDisplayDiscovery(
            backend=FakeDisplayBackend(
                records=(
                    NativeDisplayRecord(
                        stable_id="stable-primary",
                        rect=(0, 0, 1920, 1080),
                        work_rect=(0, 0, 1920, 1040),
                        dpi=96,
                        orientation_code=0,
                        primary=True,
                        enabled=True,
                    ),
                )
            )
        ).discover("client-trust", "ParthSurface")

        restored = display_group_from_message(display_group_to_message(group))

        self.assertEqual(restored, group)

    def test_malformed_peer_inventory_is_rejected_safely(self):
        message = {
            "machine_id": "client",
            "windows_name": "private malformed name",
            "displays": [],
        }

        with self.assertRaisesRegex(DisplayDiscoveryError, "invalid display inventory") as caught:
            display_group_from_message(message)

        self.assertNotIn("private malformed name", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

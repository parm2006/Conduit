from dataclasses import dataclass
import logging
import threading

from app.display_topology import Display, MachineDisplayGroup, NativeRect

EDD_GET_DEVICE_INTERFACE_NAME = 0x00000001
logger = logging.getLogger(__name__)


class DisplayDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class NativeDisplayRecord:
    stable_id: str
    rect: tuple[int, int, int, int]
    work_rect: tuple[int, int, int, int]
    dpi: int
    orientation_code: int
    primary: bool
    enabled: bool


class WindowsDisplayDiscovery:
    def __init__(self, backend=None):
        self._backend = backend or _Win32DisplayBackend()

    def discover(self, machine_id, windows_name):
        try:
            records = self._backend.snapshot()
            displays = tuple(self._translate(record) for record in records)
            return MachineDisplayGroup(
                machine_id=machine_id,
                windows_name=windows_name,
                displays=displays,
            )
        except DisplayDiscoveryError:
            raise
        except Exception as error:
            raise DisplayDiscoveryError("display discovery failed") from error

    @staticmethod
    def _translate(record):
        orientations = {0: 0, 1: 90, 2: 180, 3: 270}
        return Display(
            display_id=record.stable_id,
            rect=NativeRect(*record.rect),
            work_rect=NativeRect(*record.work_rect),
            dpi_percent=round(record.dpi * 100 / 96),
            orientation=orientations[record.orientation_code],
            primary=record.primary,
            enabled=record.enabled,
        )


class DisplayChangeMonitor:
    """Poll Windows display inventory without changing active routing."""

    def __init__(
        self,
        discovery,
        machine_id,
        windows_name,
        on_change,
        *,
        interval=1.0,
    ):
        self.discovery = discovery
        self.machine_id = machine_id
        self.windows_name = windows_name
        self.on_change = on_change
        self.interval = float(interval)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._baseline = None

    @property
    def running(self):
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self, initial_group=None):
        with self._lock:
            if self.running:
                return False
            if initial_group is None:
                initial_group = self.discovery.discover(
                    self.machine_id,
                    self.windows_name,
                )
            self._baseline = initial_group
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="ConduitDisplayMonitor",
                daemon=True,
            )
            self._thread.start()
            return True

    def update_baseline(self, group):
        with self._lock:
            self._baseline = group

    def stop(self):
        with self._lock:
            thread = self._thread
            if thread is None:
                return False
            self._stop_event.set()
        if thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.interval * 2))
        with self._lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None
        return True

    def _run(self):
        try:
            while not self._stop_event.wait(self.interval):
                try:
                    group = self.discovery.discover(
                        self.machine_id,
                        self.windows_name,
                    )
                except DisplayDiscoveryError as error:
                    logger.warning(
                        "Could not poll display inventory (%s)",
                        type(error).__name__,
                    )
                    continue
                with self._lock:
                    previous = self._baseline
                    if group == previous:
                        continue
                    self._baseline = group
                try:
                    delivered = self.on_change(group)
                    if delivered is False:
                        with self._lock:
                            if self._baseline == group:
                                self._baseline = previous
                except Exception as error:
                    with self._lock:
                        if self._baseline == group:
                            self._baseline = previous
                    logger.error(
                        "Display change callback failed (%s)",
                        type(error).__name__,
                        exc_info=True,
                    )
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None


def display_group_to_message(group):
    return {
        "machine_id": group.machine_id,
        "windows_name": group.windows_name,
        "displays": [
            {
                "display_id": display.display_id,
                "rect": _rect_to_message(display.rect),
                "work_rect": (
                    _rect_to_message(display.work_rect)
                    if display.work_rect is not None
                    else None
                ),
                "dpi_percent": display.dpi_percent,
                "orientation": display.orientation,
                "primary": display.primary,
                "enabled": display.enabled,
            }
            for display in group.displays
        ],
    }


def display_group_from_message(message):
    try:
        machine_id = _required_text(message["machine_id"])
        windows_name = _required_text(message["windows_name"])
        stored_displays = message["displays"]
        if not isinstance(stored_displays, list) or not stored_displays:
            raise ValueError("display list is empty")
        displays = []
        for stored in stored_displays:
            dpi_percent = _required_int(stored["dpi_percent"])
            orientation = _required_int(stored["orientation"])
            if dpi_percent <= 0 or orientation not in (0, 90, 180, 270):
                raise ValueError("invalid display metrics")
            if type(stored["primary"]) is not bool or type(stored["enabled"]) is not bool:
                raise ValueError("invalid display flags")
            work_rect = stored.get("work_rect")
            displays.append(
                Display(
                    display_id=_required_text(stored["display_id"]),
                    rect=_rect_from_message(stored["rect"]),
                    work_rect=(
                        _rect_from_message(work_rect)
                        if work_rect is not None
                        else None
                    ),
                    dpi_percent=dpi_percent,
                    orientation=orientation,
                    primary=stored["primary"],
                    enabled=stored["enabled"],
                )
            )
        return MachineDisplayGroup(machine_id, windows_name, tuple(displays))
    except Exception as error:
        raise DisplayDiscoveryError("invalid display inventory") from error


def _rect_to_message(rect):
    return [rect.left, rect.top, rect.right, rect.bottom]


def _rect_from_message(values):
    if not isinstance(values, list) or len(values) != 4:
        raise ValueError("invalid display rectangle")
    left, top, right, bottom = (_required_int(value) for value in values)
    if right <= left or bottom <= top:
        raise ValueError("invalid display rectangle")
    return NativeRect(left, top, right, bottom)


def _required_text(value):
    if not isinstance(value, str) or not value:
        raise ValueError("missing text value")
    return value


def _required_int(value):
    if type(value) is not int:
        raise ValueError("integer required")
    return value


class _Win32DisplayBackend:
    def snapshot(self):
        import ctypes

        import win32api
        import win32con

        records = []
        for monitor_handle, _device_context, _rect in win32api.EnumDisplayMonitors():
            info = win32api.GetMonitorInfo(monitor_handle)
            device_name = info["Device"]
            device = win32api.EnumDisplayDevices(
                device_name,
                0,
                EDD_GET_DEVICE_INTERFACE_NAME,
            )
            settings = win32api.EnumDisplaySettings(
                device_name,
                win32con.ENUM_CURRENT_SETTINGS,
            )
            dpi = self._effective_dpi(ctypes, monitor_handle)
            records.append(
                NativeDisplayRecord(
                    stable_id=device.DeviceID or device.DeviceKey or device_name,
                    rect=tuple(info["Monitor"]),
                    work_rect=tuple(info["Work"]),
                    dpi=dpi,
                    orientation_code=settings.DisplayOrientation,
                    primary=bool(info["Flags"] & win32con.MONITORINFOF_PRIMARY),
                    enabled=True,
                )
            )
        return tuple(records)

    @staticmethod
    def _effective_dpi(ctypes_module, monitor_handle):
        try:
            shcore = ctypes_module.WinDLL("shcore", use_last_error=True)
            dpi_x = ctypes_module.c_uint()
            dpi_y = ctypes_module.c_uint()
            result = shcore.GetDpiForMonitor(
                ctypes_module.c_void_p(int(monitor_handle)),
                0,
                ctypes_module.byref(dpi_x),
                ctypes_module.byref(dpi_y),
            )
            if result == 0 and dpi_x.value:
                return dpi_x.value
        except (AttributeError, OSError):
            pass
        return 96

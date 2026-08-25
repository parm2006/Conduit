import hashlib
import uuid


def stable_machine_id(machine_guid, fallback):
    source = str(machine_guid or fallback).strip().casefold()
    if not source:
        raise ValueError("machine identity source cannot be empty")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return f"windows:{digest}"


def windows_machine_id():
    machine_guid = None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
    except (ImportError, OSError):
        pass
    fallback = f"node:{uuid.getnode():012x}"
    return stable_machine_id(machine_guid, fallback)

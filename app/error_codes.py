"""Stable user-facing codes for Conduit's status console."""

OK = "OK"
UNKNOWN = "XX"

SERVER_INVALID_PORT = "1"
CLIENT_INVALID_PORT = "2"
SERVER_PASSWORD_REQUIRED = "3"
CLIENT_PASSWORD_REQUIRED = "4"
FIREWALL_ACTION_CANCELLED = "5"
FIREWALL_ACTION_FAILED = "6"
FIREWALL_START_CANCELLED = "7"
FIREWALL_START_FAILED = "8"
SERVER_START_FAILED = "9"
REMOTE_VIEW_START_FAILED = "10"
CLIENT_CONNECTION_FAILED = "11"
CLIENT_DISPLAYS_CHANGED = "12"
SERVER_DISPLAY_RESCAN_FAILED = "13"
CLIENT_DISPLAY_RESCAN_FAILED = "14"
CLIENT_DISPLAY_RESCAN_TIMEOUT = "15"
CLIENT_TOPOLOGY_POSITION_UNAVAILABLE = "16"
TOPOLOGY_DISCONNECTED = "17"
TOPOLOGY_LOCAL_APPLY_FAILED = "18"
CLIENT_TOPOLOGY_REJECTED = "19"
CLIENT_TOPOLOGY_DISCONNECTED = "20"
PAIRING_IDENTITY_CLEAR_FAILED = "21"
REMOTE_VIDEO_UNAVAILABLE = "22"


def format_error_code(error_code=UNKNOWN, client_name=None, client_specific=False):
    """Return a display-safe error code with an optional Client initial."""
    if error_code == OK:
        return OK
    if isinstance(error_code, bool):
        return UNKNOWN
    value = str(error_code).strip() if error_code is not None else ""
    if not value.isdigit() or int(value) <= 0:
        return UNKNOWN
    value = str(int(value))
    if not client_specific:
        return value
    name = str(client_name).strip() if client_name is not None else ""
    initial = name[0].upper() if name and name[0].isalnum() else "X"
    return f"{value}.{initial}"

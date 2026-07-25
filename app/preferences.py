"""Non-secret local DeskFlow UI preferences."""

import json
import logging
import os
from pathlib import Path
import uuid

from app.safe_errors import error_name


logger = logging.getLogger(__name__)
VALID_ROLES = frozenset(("server", "client"))
VALID_CLIENT_POSITIONS = frozenset(("top", "left", "right", "bottom"))


class UserPreferences:
    def __init__(self, root=None):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DeskFlow"
        self.root = Path(root or base).resolve()
        self.path = self.root / "preferences.json"

    def load_role(self):
        role = self._load_values().get("last_successful_role")
        return role if role in VALID_ROLES else None

    def load_client_position(self):
        position = self._load_values().get("client_position")
        return position if position in VALID_CLIENT_POSITIONS else "right"

    def load_server_port(self):
        try:
            port = int(self._load_values().get("server_port", 5000))
            return port if 1 <= port <= 65535 else 5000
        except (TypeError, ValueError):
            return 5000

    def save_role(self, role):
        if role not in VALID_ROLES:
            raise ValueError("role must be server or client")
        self._save_value("last_successful_role", role)

    def save_client_position(self, position):
        if position not in VALID_CLIENT_POSITIONS:
            raise ValueError("client position must be top, left, right, or bottom")
        self._save_value("client_position", position)

    def save_server_port(self, port):
        try:
            val = int(port)
            if not (1 <= val <= 65535):
                raise ValueError("port must be between 1 and 65535")
            self._save_value("server_port", val)
        except (TypeError, ValueError) as error:
            raise ValueError("port must be an integer between 1 and 65535") from error

    def _load_values(self):
        if not self.path.exists():
            return {}
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            return values if isinstance(values, dict) else {}
        except Exception as error:
            logger.error("Could not load DeskFlow preferences (%s)", error_name(error))
            return {}

    def _save_value(self, key, value):
        self.root.mkdir(parents=True, exist_ok=True)
        values = self._load_values()
        values[key] = value
        payload = json.dumps(values, separators=(",", ":"))
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

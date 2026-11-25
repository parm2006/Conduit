"""Minimal peer log exchange, build SHA verification, and Win32 DPI context."""

import base64
import hashlib
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import uuid
import zlib
from functools import lru_cache
from pathlib import Path

from app.version import PRODUCT_VERSION

logger = logging.getLogger(__name__)

MAX_CONDUIT_LOG_BYTES = 1024 * 1024
MAX_DECOMPRESSED_BYTES = 8 * 1024 * 1024
DEFAULT_PREAMBLE_BYTES = 16 * 1024
DEFAULT_STALL_EXCHANGE_COOLDOWN = 30.0


def default_log_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Conduit"


def default_conduit_log_path(log_root=None) -> Path:
    root = Path(log_root) if log_root is not None else default_log_root()
    return root / "conduit.log"


def default_peer_logs_dir(log_root=None) -> Path:
    root = Path(log_root) if log_root is not None else default_log_root()
    return root / "peer_logs"


def win32_dpi_context() -> dict:
    info = {"process_dpi_awareness": None, "system_dpi": None}
    if sys.platform != "win32":
        return info
    try:
        import ctypes

        awareness = ctypes.c_int()
        if ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness)) == 0:
            info["process_dpi_awareness"] = int(awareness.value)
    except Exception:
        pass
    try:
        import ctypes

        info["system_dpi"] = int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        pass
    return info


def _resolve_git_dir(repo_root: Path) -> tuple[Path | None, Path | None]:
    dot_git = repo_root / ".git"
    if dot_git.is_dir():
        return dot_git, dot_git
    if dot_git.is_file():
        try:
            text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None, None
        if text.startswith("gitdir:"):
            raw = text[len("gitdir:"):].strip()
            git_dir = Path(raw)
            if not git_dir.is_absolute():
                git_dir = (repo_root / git_dir).resolve()
            common_dir = git_dir
            common_file = git_dir / "commondir"
            if common_file.is_file():
                try:
                    rel = common_file.read_text(encoding="utf-8", errors="replace").strip()
                    common_dir = (git_dir / rel).resolve()
                except OSError:
                    pass
            return git_dir, common_dir
    return None, None


def _resolve_git_head(repo_root: Path) -> tuple[str, str]:
    git_dir, common_dir = _resolve_git_dir(repo_root)
    if git_dir is None:
        return "packaged" if getattr(sys, "frozen", False) else "unknown", ""
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return "unknown", ""
    if not head.startswith("ref:"):
        return head[:12] or "unknown", "detached"

    ref_name = head[len("ref:"):].strip()
    branch = ref_name.removeprefix("refs/heads/")
    for base in (git_dir, common_dir):
        if base is None:
            continue
        ref_file = base / ref_name
        if ref_file.is_file():
            try:
                sha = ref_file.read_text(encoding="utf-8", errors="replace").strip()
                return sha[:12] or "unknown", branch
            except OSError:
                pass
    if common_dir is not None:
        packed = common_dir / "packed-refs"
        if packed.is_file():
            try:
                for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not line or line.startswith(("#", "^")):
                        continue
                    sha, _, ref = line.partition(" ")
                    if ref.strip() == ref_name:
                        return sha[:12] or "unknown", branch
            except OSError:
                pass
    return "unknown", branch


@lru_cache(maxsize=4)
def _cached_repo_build(repo_root_str: str) -> tuple[str, str, str]:
    repo_root = Path(repo_root_str)
    commit, branch = _resolve_git_head(repo_root)
    dll_path = repo_root / "app" / "bin" / "conduit_streamer.dll"
    if not dll_path.is_file():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cand = Path(meipass) / "app" / "bin" / "conduit_streamer.dll"
            if cand.is_file():
                dll_path = cand
    dll_sha = ""
    if dll_path.is_file():
        try:
            dll_sha = hashlib.sha256(dll_path.read_bytes()).hexdigest()[:12]
        except OSError:
            dll_sha = ""
    return commit, branch, dll_sha


def local_build_info(repo_root=None) -> dict:
    root = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parent.parent
    commit, branch, dll_sha = _cached_repo_build(str(root))
    dpi = win32_dpi_context()
    return {
        "version": PRODUCT_VERSION,
        "commit": commit,
        "branch": branch,
        "dll_sha256": dll_sha,
        "stage1_mode": os.environ.get("CONDUIT_VIDEO_STAGE1", "baseline").lower(),
        "hostname": socket.gethostname(),
        "dpi_awareness": dpi["process_dpi_awareness"],
        "system_dpi": dpi["system_dpi"],
    }


def format_build_summary(info: dict | None) -> str:
    if not isinstance(info, dict):
        return "unknown"
    commit = info.get("commit") or "unknown"
    branch = info.get("branch") or ""
    branch_part = f" ({branch})" if branch else ""
    dll = info.get("dll_sha256") or "none"
    mode = info.get("stage1_mode") or "baseline"
    return f"v{info.get('version', '?')}@{commit}{branch_part} dll={dll} mode={mode}"


def builds_match(local: dict | None, peer: dict | None) -> bool | None:
    if not isinstance(local, dict) or not isinstance(peer, dict):
        return None
    lc, pc = local.get("commit"), peer.get("commit")
    if not lc or not pc or "unknown" in (lc, pc):
        return None
    return lc == pc and (local.get("dll_sha256") or "") == (peer.get("dll_sha256") or "")


def current_log_offset(log_path=None) -> int:
    path = Path(log_path) if log_path is not None else default_conduit_log_path()
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def flush_logging_handlers() -> None:
    seen = set()
    loggers = [logging.getLogger(), *logging.Logger.manager.loggerDict.values()]
    for log_obj in loggers:
        if not isinstance(log_obj, logging.Logger):
            continue
        for handler in list(getattr(log_obj, "handlers", ()) or ()):
            if id(handler) in seen:
                continue
            seen.add(id(handler))
            try:
                handler.flush()
            except Exception:
                pass


def read_session_log_bytes(
    path,
    start_offset: int = 0,
    preamble_bytes: int = DEFAULT_PREAMBLE_BYTES,
    max_bytes: int = MAX_CONDUIT_LOG_BYTES,
) -> bytes:
    if not path or max_bytes <= 0:
        return b""
    file_path = Path(path)
    try:
        size = file_path.stat().st_size
    except OSError:
        return b""
    if size <= 0:
        return b""
    try:
        with file_path.open("rb") as fh:
            if start_offset <= 0 or size < start_offset:
                fh.seek(max(0, size - max_bytes))
                return fh.read(max_bytes)
            fh.seek(max(0, start_offset - max(0, int(preamble_bytes))))
            data = fh.read()
    except OSError:
        return b""
    if len(data) <= max_bytes:
        return data
    marker = b"\n...[truncated middle of session log]...\n"
    head_len = min(8192, max_bytes // 4)
    tail_len = max(0, max_bytes - head_len - len(marker))
    return data[:head_len] + marker + data[-tail_len:] if tail_len > 0 else data[-max_bytes:]


def compress_log_bytes(raw: bytes) -> str:
    return base64.b64encode(zlib.compress(raw, level=6)).decode("ascii") if raw else ""


def decompress_log_bytes(encoded) -> bytes:
    if not isinstance(encoded, str) or not encoded:
        return b""
    try:
        compressed = base64.b64decode(encoded.encode("ascii"), validate=True)
        out = zlib.decompressobj().decompress(compressed, MAX_DECOMPRESSED_BYTES + 1)
        if len(out) > MAX_DECOMPRESSED_BYTES:
            return out[:MAX_DECOMPRESSED_BYTES] + b"\n...[truncated decompressed cap]...\n"
        return out
    except Exception:
        return b""


def _sanitize_segment(value: str, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._")
    return cleaned[:48] or fallback


def _log_build_comparison(prefix: str, local_build: dict, peer_build: dict | None, peer_label: str) -> bool | None:
    matched = builds_match(local_build, peer_build)
    msg = "%s peer=%s match=%s local=[%s] peer=[%s]"
    args = (prefix, peer_label, matched, format_build_summary(local_build), format_build_summary(peer_build))
    if matched is False:
        logger.warning(msg, *args)
    else:
        logger.info(msg, *args)
    return matched


class PeerLogExchange:
    def __init__(self, *, role: str, machine_id: str, log_root=None, hostname: str | None = None, repo_root=None):
        self.role = str(role or "")
        self.machine_id = str(machine_id or "")
        self.log_root = log_root
        self.hostname = hostname
        self.repo_root = repo_root
        self._lock = threading.Lock()
        self._session_start_offset = 0
        self._pending_events: dict[str, threading.Event] = {}
        self._saved_paths: dict[str, Path] = {}

    def _build_info(self) -> dict:
        info = dict(local_build_info(self.repo_root))
        if self.hostname:
            info["hostname"] = self.hostname
        return info

    def mark_session_start(self) -> int:
        offset = current_log_offset(default_conduit_log_path(self.log_root))
        with self._lock:
            self._session_start_offset = offset
        return offset

    @property
    def session_start_offset(self) -> int:
        with self._lock:
            return self._session_start_offset

    @staticmethod
    def is_live_transport(network) -> bool:
        if network is None:
            return False
        if getattr(network, "_debug_log_exchange_allow_wait", False) is True:
            return True
        try:
            from app.network import NetworkClient, NetworkServer
        except Exception:
            return False
        return isinstance(network, (NetworkServer, NetworkClient))

    def build_verify_message(self) -> dict:
        return {
            "type": "peer_build_info",
            "role": self.role,
            "machine_id": self.machine_id,
            "build": self._build_info(),
        }

    def handle_build_info(self, data) -> bool | None:
        if not isinstance(data, dict):
            return None
        peer_build = data.get("build")
        peer_label = f"{data.get('role', '?')}:{peer_build.get('hostname', '?') if isinstance(peer_build, dict) else '?'}"
        return _log_build_comparison("[remote-diag] peer_build", self._build_info(), peer_build, peer_label)

    def build_message(self, *, exchange_id: str, reply_requested: bool, reason: str) -> dict:
        flush_logging_handlers()
        raw = read_session_log_bytes(default_conduit_log_path(self.log_root), start_offset=self.session_start_offset)
        build = self._build_info()
        return {
            "type": "debug_log_exchange",
            "exchange_id": str(exchange_id),
            "reply_requested": bool(reply_requested),
            "role": self.role,
            "hostname": build["hostname"],
            "machine_id": self.machine_id,
            "reason": str(reason or ""),
            "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "build": build,
            "conduit_log_z": compress_log_bytes(raw),
        }

    def handle_incoming(self, data, *, send_reply=None) -> Path | None:
        if not isinstance(data, dict):
            return None
        flush_logging_handlers()
        peer_logs_dir = default_peer_logs_dir(self.log_root)
        peer_logs_dir.mkdir(parents=True, exist_ok=True)

        exchange_id = str(data.get("exchange_id") or "")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        peer_role = _sanitize_segment(data.get("role", "peer"), "peer")
        peer_host = _sanitize_segment(data.get("hostname", "unknown"), "unknown")
        short_id = _sanitize_segment(exchange_id[:8], uuid.uuid4().hex[:8])

        bundle_dir = peer_logs_dir / f"{stamp}_peer-{peer_role}-{peer_host}_{short_id}"
        suffix = 1
        while bundle_dir.exists():
            suffix += 1
            bundle_dir = peer_logs_dir / f"{stamp}_peer-{peer_role}-{peer_host}_{short_id}_{suffix}"
        bundle_dir.mkdir(parents=True, exist_ok=True)

        peer_conduit = decompress_log_bytes(data.get("conduit_log_z"))
        local_conduit = read_session_log_bytes(
            default_conduit_log_path(self.log_root),
            start_offset=self.session_start_offset,
        )
        (bundle_dir / "peer_conduit.log").write_bytes(peer_conduit)
        (bundle_dir / "local_conduit.log").write_bytes(local_conduit)

        local_build = self._build_info()
        peer_build = data.get("build") if isinstance(data.get("build"), dict) else None
        build_match = _log_build_comparison(
            "[remote-diag][log-exchange] build",
            local_build,
            peer_build,
            f"{peer_role}:{peer_host}",
        )
        meta = {
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "exchange_id": exchange_id,
            "reason": str(data.get("reason") or ""),
            "reply_requested": bool(data.get("reply_requested")),
            "build_match": build_match,
            "peer": {
                "role": str(data.get("role") or ""),
                "hostname": str(data.get("hostname") or ""),
                "machine_id": str(data.get("machine_id") or ""),
                "sent_at": str(data.get("sent_at") or ""),
                "conduit_bytes": len(peer_conduit),
                "build": peer_build,
            },
            "local": {
                "role": self.role,
                "hostname": local_build["hostname"],
                "machine_id": self.machine_id,
                "conduit_bytes": len(local_conduit),
                "start_offset": self.session_start_offset,
                "build": local_build,
            },
        }
        (bundle_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        logger.info(
            "[remote-diag][log-exchange] Saved bundle=%s (peer=%dB local=%dB)",
            bundle_dir, len(peer_conduit), len(local_conduit),
        )

        if data.get("reply_requested") and callable(send_reply):
            reply = self.build_message(
                exchange_id=exchange_id,
                reply_requested=False,
                reason=f"reply:{data.get('reason', '')}",
            )
            try:
                send_reply(reply)
            except Exception as error:
                logger.warning("[remote-diag][log-exchange] Reply failed (%s)", type(error).__name__)

        if exchange_id:
            with self._lock:
                self._saved_paths[exchange_id] = bundle_dir
                event = self._pending_events.get(exchange_id)
            if event is not None:
                event.set()
        return bundle_dir

    def initiate_exchange(self, *, targets, reason: str, timeout: float = 2.0) -> list[Path]:
        started = time.monotonic()
        wait_items: list[tuple[str, threading.Event]] = []
        requested = 0
        try:
            for label, send_fn, can_wait in targets or ():
                if not callable(send_fn):
                    continue
                exchange_id = uuid.uuid4().hex
                event = threading.Event()
                with self._lock:
                    self._pending_events[exchange_id] = event
                payload = self.build_message(exchange_id=exchange_id, reply_requested=True, reason=reason)
                try:
                    sent = bool(send_fn(payload))
                except Exception as error:
                    logger.warning("[remote-diag][log-exchange] Send failed target=%s (%s)", label, type(error).__name__)
                    sent = False
                if sent:
                    requested += 1
                    if can_wait:
                        wait_items.append((exchange_id, event))
                        continue
                with self._lock:
                    self._pending_events.pop(exchange_id, None)

            deadline = started + max(0.0, float(timeout))
            for _eid, event in wait_items:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                event.wait(remaining)

            saved = []
            for exchange_id, _event in wait_items:
                with self._lock:
                    path = self._saved_paths.pop(exchange_id, None)
                if path is not None:
                    saved.append(path)
            logger.info(
                "[remote-diag][log-exchange] Completed reason=%s requested=%d received=%d elapsed=%.3fs",
                reason, requested, len(saved), time.monotonic() - started,
            )
            return saved
        finally:
            with self._lock:
                for exchange_id, _event in wait_items:
                    self._pending_events.pop(exchange_id, None)
                    self._saved_paths.pop(exchange_id, None)


def attach_peer_log_exchange(endpoint, control_network, *, role: str, machine_id: str, **kwargs) -> PeerLogExchange:
    exchange = PeerLogExchange(role=role, machine_id=machine_id, **kwargs)
    endpoint.peer_log_exchange = exchange

    def _on_exchange(data):
        if not isinstance(data, dict):
            return None
        sid = data.get("session_id")
        return exchange.handle_incoming(
            data,
            send_reply=(
                (lambda msg, s=sid: control_network.send_message(msg, session_id=s))
                if sid is not None
                else control_network.send_message
            ),
        )

    control_network.register_callback("debug_log_exchange", _on_exchange)
    control_network.register_callback("peer_build_info", exchange.handle_build_info)
    return exchange


def send_live_build_info(endpoint, session_id=None) -> bool:
    exchange = getattr(endpoint, "peer_log_exchange", None)
    control_network = getattr(endpoint, "control_network", None)
    if exchange is None or not PeerLogExchange.is_live_transport(control_network):
        return False
    msg = exchange.build_verify_message()
    try:
        return bool(
            control_network.send_message(msg, session_id=session_id)
            if session_id is not None
            else control_network.send_message(msg)
        )
    except Exception:
        return False


def exchange_endpoint_logs(endpoint, *, reason: str, timeout: float = 2.0) -> list[Path]:
    exchange = getattr(endpoint, "peer_log_exchange", None)
    control_network = getattr(endpoint, "control_network", None)
    if (
        exchange is None
        or not getattr(endpoint, "control_connected", False)
        or not PeerLogExchange.is_live_transport(control_network)
    ):
        return []
    try:
        registry = getattr(endpoint, "session_registry", None)
        targets = []
        if registry is not None:
            sessions = list(registry.ready_sessions()) or [
                s for s in registry.active_sessions() if getattr(s, "control_lane", None) is not None
            ]
            for session in sessions:
                sid = session.session_id
                targets.append((str(sid)[:8], lambda m, s=sid: control_network.send_message(m, session_id=s), True))
        elif hasattr(endpoint, "session_registry") or getattr(control_network, "is_server", False):
            targets.append(("legacy", control_network.send_message, True))
        else:
            targets.append(("server", control_network.send_message, True))
        return exchange.initiate_exchange(targets=targets, reason=reason, timeout=timeout) if targets else []
    except Exception as error:
        logger.warning("[remote-diag][log-exchange] Exchange failed reason=%s (%s)", reason, type(error).__name__)
        return []


def async_exchange_on_stall(endpoint, *, reason: str = "remote_video_stall", cooldown: float = DEFAULT_STALL_EXCHANGE_COOLDOWN) -> bool:
    exchange = getattr(endpoint, "peer_log_exchange", None)
    control_network = getattr(endpoint, "control_network", None)
    if (
        exchange is None
        or not getattr(endpoint, "control_connected", False)
        or not PeerLogExchange.is_live_transport(control_network)
    ):
        return False
    now = time.monotonic()
    last = getattr(exchange, "_last_stall_exchange_at", 0.0)
    if now - last < cooldown:
        return False
    exchange._last_stall_exchange_at = now
    threading.Thread(
        target=exchange_endpoint_logs,
        kwargs={"endpoint": endpoint, "reason": reason, "timeout": 2.0},
        name="remote-diag-log-exchange",
        daemon=True,
    ).start()
    return True

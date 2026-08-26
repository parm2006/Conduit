import sys
import os
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Ensure the project root is in the path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))


def configure_runtime_logging(log_root=None):
    root = Path(
        log_root
        or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Conduit"
    )
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "conduit.log"
    handlers = [
        RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s %(levelname)s [%(threadName)s] "
            "%(name)s: %(message)s"
        ),
        handlers=handlers,
        force=True,
    )
    logging.getLogger(__name__).info(
        "Conduit diagnostic logging started (log=%s)",
        log_path,
    )
    return log_path


def main(argv=None, *, helper_runner=None, gui_runner=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--conduit-firewall-helper"]:
        if helper_runner is None:
            from app.firewall_helper import run_firewall_helper

            helper_runner = run_firewall_helper
        return helper_runner(arguments[1:])

    configure_runtime_logging()
    if gui_runner is None:
        from app.gui import run_gui

        gui_runner = run_gui
    gui_runner()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

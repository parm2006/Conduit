import sys
import os
import logging

# Ensure the project root is in the path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))


def configure_runtime_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def main(argv=None, *, helper_runner=None, gui_runner=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["--deskflow-firewall-helper"]:
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

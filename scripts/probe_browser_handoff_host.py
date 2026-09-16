"""PyInstaller entry point for the disposable native-messaging probe host."""

from probe_browser_handoff import main


if __name__ == "__main__":
    raise SystemExit(main(["--native-host"]))

"""Console entry point for the separately packaged browser native host."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.browser_handoff.local_bridge import NamedPipeClient, default_pipe_name
from app.browser_handoff.native_host import main


if __name__ == "__main__":
    raise SystemExit(main(lambda: NamedPipeClient(default_pipe_name())))

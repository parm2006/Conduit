"""Compile and run the real C++ queue tests without requiring a GPU or DLL."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class NativeVideoQueueTests(unittest.TestCase):
    def test_reference_recovery_bounded_backlog_and_shutdown(self):
        source = Path(__file__).with_name("native_video_queue.cpp").resolve()
        with tempfile.TemporaryDirectory(prefix="conduit-video-queue-") as directory:
            if os.name == "nt":
                candidates = [
                    Path("C:/Program Files (x86)/Microsoft Visual Studio/18/BuildTools/VC/Auxiliary/Build/vcvars64.bat"),
                    Path("C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Auxiliary/Build/vcvars64.bat"),
                ]
                vcvars = next((path for path in candidates if path.is_file()), None)
                if vcvars is None:
                    self.skipTest("MSVC build environment unavailable")
                batch = Path(directory) / "compile.bat"
                batch.write_text(
                    f'@call "{vcvars}" >nul\n'
                    f'@cl /nologo /std:c++17 /EHsc /W4 "{source}" /Fe:queue.exe /Fo:queue.obj\n',
                    encoding="ascii",
                )
                command = ["cmd", "/c", str(batch)]
                executable = Path(directory) / "queue.exe"
            else:
                compiler = shutil.which("c++")
                if compiler is None:
                    self.skipTest("C++ compiler unavailable")
                executable = Path(directory) / "queue"
                command = [compiler, "-std=c++17", "-pthread", str(source), "-o", str(executable)]
            compiled = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=90)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            tested = subprocess.run([str(executable)], cwd=directory, capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)


if __name__ == "__main__":
    unittest.main()

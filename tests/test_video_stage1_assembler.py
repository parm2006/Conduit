import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class Stage1AssemblerTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows native code")
    def test_compiled_assembler_callback_can_reenter(self):
        root = Path(__file__).resolve().parents[1]
        vcvars = Path("C:/Program Files (x86)/Microsoft Visual Studio/18/BuildTools/VC/Auxiliary/Build/vcvars64.bat")
        if not vcvars.exists():
            self.skipTest("MSVC toolchain unavailable")
        with tempfile.TemporaryDirectory(prefix="conduit-assembler-") as directory:
            batch = Path(directory) / "compile.bat"
            source = root / "native/conduit_streamer"
            batch.write_text(f'@call "{vcvars}" >nul\n@cl /nologo /std:c++17 /EHsc /W4 /I"{source}" "{root / "tests/native_stage1_assembler.cpp"}" "{source / "packet_assembler.cpp"}" /Fe:test.exe\n', encoding="ascii")
            compiled = subprocess.run(["cmd", "/c", str(batch)], cwd=directory, capture_output=True, text=True, timeout=90)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            tested = subprocess.run([str(Path(directory) / "test.exe")], capture_output=True, text=True, timeout=3)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class CursorSourceVisibilityTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'MSVC native policy test')
    def test_hidden_source_is_distinct_from_image_owned_pointer(self):
        root = Path(__file__).resolve().parents[1]
        vcvars = Path('C:/Program Files (x86)/Microsoft Visual Studio/18/BuildTools/VC/Auxiliary/Build/vcvars64.bat')
        if not vcvars.exists():
            self.skipTest('MSVC toolchain unavailable')
        with tempfile.TemporaryDirectory(prefix='conduit-cursor-') as directory:
            batch = Path(directory) / 'compile.bat'
            batch.write_text(
                f'@call "{vcvars}" >nul\n'
                f'@cl /nologo /std:c++17 /EHsc /W4 /c '
                f'"{root / "native/conduit_streamer/dxgi_capture.cpp"}" /Fo:dxgi_capture.obj\n'
                '@if errorlevel 1 exit /b %errorlevel%\n'
                f'@cl /nologo /std:c++17 /EHsc /W4 /I"{root / "native/conduit_streamer"}" '
                f'"{root / "tests/native_cursor_source_visibility.cpp"}" /Fe:cursor.exe\n',
                encoding='ascii')
            compiled = subprocess.run(['cmd', '/c', str(batch)], cwd=directory,
                                      capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            tested = subprocess.run([str(Path(directory) / 'cursor.exe')],
                                    capture_output=True, text=True, timeout=3)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)


if __name__ == '__main__':
    unittest.main()

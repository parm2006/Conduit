"""Compile and exercise the production AES-GCM UDP transport over loopback."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows native transport')
class NativeVideoTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        vcvars = Path('C:/Program Files (x86)/Microsoft Visual Studio/18/BuildTools/VC/Auxiliary/Build/vcvars64.bat')
        if not vcvars.exists():
            raise unittest.SkipTest('MSVC toolchain unavailable')
        temporary = tempfile.TemporaryDirectory(prefix='conduit-transport-')
        cls.addClassCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        source = root / 'native/conduit_streamer'
        batch = directory / 'compile.bat'
        batch.write_text(
            f'@call "{vcvars}" >nul\n'
            f'@cl /nologo /std:c++17 /EHsc /W4 /DWIN32_LEAN_AND_MEAN /I"{source}" '
            f'"{root / "tests/native_video_transport.cpp"}" '
            f'"{source / "udp_transport.cpp"}" "{source / "crypto_aead.cpp"}" '
            f'"{source / "packet_assembler.cpp"}" /Fe:transport.exe /link ws2_32.lib bcrypt.lib\n',
            encoding='ascii',
        )
        compiled = subprocess.run(['cmd', '/c', str(batch)], cwd=directory,
                                  capture_output=True, text=True, timeout=90)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)
        cls.executable = directory / 'transport.exe'

    def probe(self, mode):
        result = subprocess.run([str(self.executable), mode], capture_output=True,
                                text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'{mode}:', result.stdout)

    def test_reordered_fragmented_payload_and_all_cursor_modes(self):
        self.probe('roundtrip')

    def test_modified_ciphertext_is_rejected_then_keyframe_recovers(self):
        self.probe('tamper_ciphertext')

    def test_modified_cursor_header_is_authenticated_then_keyframe_recovers(self):
        self.probe('tamper_header')


if __name__ == '__main__':
    unittest.main()

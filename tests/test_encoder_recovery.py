"""A real encoder-output failure must not leak damaged reference chains."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class EncoderRecoveryTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows Media Foundation required')
    def test_lost_encoder_output_recovers_with_idr_and_matching_pixels(self):
        root = Path(__file__).resolve().parents[1]
        vcvars = Path('C:/Program Files (x86)/Microsoft Visual Studio/18/BuildTools/VC/Auxiliary/Build/vcvars64.bat')
        if not vcvars.exists():
            self.skipTest('MSVC toolchain unavailable')
        native = root / 'native/conduit_streamer'
        with tempfile.TemporaryDirectory(prefix='conduit-encoder-recovery-') as temporary:
            directory = Path(temporary)
            batch = directory / 'compile.bat'
            batch.write_text(
                f'@call "{vcvars}" >nul\n'
                f'@cl /nologo /std:c++17 /EHsc /W4 /O2 /I"{native}" '
                f'"{root / "tests/native_encoder_recovery.cpp"}" '
                f'"{native / "h264_encoder.cpp"}" "{native / "h264_decoder.cpp"}" '
                '/Fe:recovery.exe /link d3d11.lib dxgi.lib mfplat.lib mfuuid.lib wmcodecdspuuid.lib ole32.lib\n',
                encoding='ascii',
            )
            compiled = subprocess.run(['cmd', '/c', str(batch)], cwd=directory,
                                      capture_output=True, text=True, timeout=90)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            for failure in ('consumed', 'pending'):
                with self.subTest(failure=failure):
                    result = subprocess.run([str(directory / 'recovery.exe'), failure], capture_output=True,
                                            text=True, timeout=30)
                    if result.returncode == 2 and ('DEVICE_FAIL' in result.stdout or 'DECODE_DEVICE_FAIL' in result.stdout):
                        self.skipTest('D3D11 hardware device unavailable')
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('OUTPUT_FAILURE', result.stdout)
                    self.assertIn('cleanpoint=1 idr=1', result.stdout)
                    self.assertIn('mismatches=0 decode_failures=0 assertions=0', result.stdout)

"""Compile and execute the actual GPU-free Claude Stage 1 regression tests."""
import os
from pathlib import Path
import subprocess
import unittest


class VideoStage1ComponentTests(unittest.TestCase):
    def test_native_components_and_probe_compile(self):
        if os.name != 'nt':
            self.skipTest('Stage 1 components require the Windows SDK')
        candidates = (
            Path('C:/Program Files (x86)/Microsoft Visual Studio/18/BuildTools/VC/Auxiliary/Build/vcvars64.bat'),
            Path('C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Auxiliary/Build/vcvars64.bat'),
            Path('C:/Program Files/Microsoft Visual Studio/2022/Professional/VC/Auxiliary/Build/vcvars64.bat'),
            Path('C:/Program Files/Microsoft Visual Studio/2022/Enterprise/VC/Auxiliary/Build/vcvars64.bat'),
        )
        if not any(path.is_file() for path in candidates):
            self.skipTest('MSVC build environment unavailable')
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ['powershell', '-NoProfile', '-File', str(root / 'scripts/test_video_stage1.ps1')],
            cwd=root, capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ALL PASSED', result.stdout)
        self.assertTrue((root / 'build/video-stage1/mft_probe.exe').is_file())

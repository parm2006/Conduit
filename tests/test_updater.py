import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.updater import Download, select_update, download_update, verified_installer, stage_installer, launch_installer


def release(version, *, preview=False, data=b'installer'):
    name = f'Conduit-v{version}-Setup.exe'
    base = f'https://github.com/parm2006/Conduit/releases/download/v{version}/'
    return dict(tag_name=f'v{version}', prerelease=preview, draft=False, assets=[
        dict(name=name, size=len(data), browser_download_url=base + name),
        dict(name='SHA256SUMS.txt', browser_download_url=base + 'SHA256SUMS.txt'),
    ])


class UpdaterTests(unittest.TestCase):
    def test_source_launch_cannot_install(self):
        with patch('app.updater.can_install', return_value=False), patch('app.updater.stage_installer') as stage:
            with self.assertRaises(RuntimeError): launch_installer(None)
            stage.assert_not_called()

    def test_install_launches_protected_copy_and_schedules_cleanup(self):
        path = Path('C:/Program Files/Conduit-Update-test/Conduit-v7.3.2-Setup.exe')
        with (patch('app.updater.can_install', return_value=True),
              patch('app.updater.stage_installer', return_value=path),
              patch('app.updater.subprocess.Popen') as launch,
              patch('win32api.GetSystemDirectory', return_value='C:/Windows/System32'),
              patch('win32file.MoveFileEx') as cleanup):
            process = launch_installer(None)
        launch.assert_called_once_with([str(path)], cwd='C:/Windows/System32')
        self.assertIs(process, launch.return_value)
        self.assertEqual(cleanup.call_count, 2)

    def test_windows_staging_uses_explicit_security_and_verifies_copy(self):
        import win32api
        import win32con
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        real_sid = win32security.CreateWellKnownSid
        # Run real Win32 secure file creation without requiring administrator
        # elevation in tests; production uses Administrators ownership.
        def test_sid(kind):
            return user if kind == win32security.WinBuiltinAdministratorsSid else real_sid(kind)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'Conduit-v7.3.2-Setup.exe'
            source.write_bytes(b'installer')
            download = Download(source, hashlib.sha256(b'installer').hexdigest(), 9)
            with patch('win32com.shell.shell.SHGetFolderPath', return_value=directory), patch('win32security.CreateWellKnownSid', side_effect=test_sid):
                staged = stage_installer(download)
            self.assertEqual(staged.read_bytes(), b'installer')
            descriptor = win32security.GetFileSecurity(str(staged), win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
            self.assertEqual(descriptor.GetSecurityDescriptorOwner(), user)
            acl = descriptor.GetSecurityDescriptorDacl()
            self.assertTrue(all(acl.GetAce(i)[2] in (user, real_sid(win32security.WinLocalSystemSid)) for i in range(acl.GetAceCount())))
            source.write_bytes(b'tampered!')
            self.assertEqual(staged.read_bytes(), b'installer')
            with patch('win32com.shell.shell.SHGetFolderPath', return_value=directory), patch('win32security.CreateWellKnownSid', side_effect=test_sid):
                with self.assertRaises(ValueError): stage_installer(download)

    def test_channels_and_numeric_preview_order(self):
        releases = [release('7.3.2-beta.2', preview=True), release('7.3.2-beta.10', preview=True), release('7.3.1')]
        self.assertIsNone(select_update(releases, '7.3.1'))
        self.assertEqual(select_update(releases, '7.3.2-beta.1').version, '7.3.2-beta.10')
        self.assertEqual(select_update([release('7.3.2')], '7.3.2-beta.10').version, '7.3.2')
        self.assertIsNone(select_update([release('7.3.1')], '7.3.2-beta.1'))

    def test_drafts_missing_checksums_and_foreign_urls_are_ignored(self):
        bad = release('9.0.0'); bad['assets'][0]['browser_download_url'] = 'https://evil.example/setup.exe'
        draft = release('8.0.0'); draft['draft'] = True
        missing = release('10.0.0'); missing['assets'].pop()
        self.assertIsNone(select_update([bad, draft, missing], '7.3.1'))

    def test_download_verifies_content_and_rechecks_before_install(self):
        update = select_update([release('7.3.2')], '7.3.1')
        digest = hashlib.sha256(b'installer').hexdigest()
        def fetch(url):
            return io.BytesIO((digest + '  ' + update.filename).encode() if url.endswith('.txt') else b'installer')
        with tempfile.TemporaryDirectory() as directory:
            downloaded = download_update(update, Path(directory), fetch=fetch)
            self.assertEqual(verified_installer(downloaded), downloaded.path)
            downloaded.path.write_bytes(b'tampered')
            with self.assertRaises(ValueError): verified_installer(downloaded)

    def test_corrupt_download_does_not_leave_executable(self):
        update = select_update([release('7.3.2')], '7.3.1')
        def fetch(url):
            return io.BytesIO(('0' * 64 + '  ' + update.filename).encode() if url.endswith('.txt') else b'installer')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError): download_update(update, Path(directory), fetch=fetch)
            self.assertEqual(list(Path(directory).rglob('*.exe')), [])


if __name__ == '__main__':
    unittest.main()

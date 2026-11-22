"""GitHub release discovery and verified NSIS installer downloads."""
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.version import PRODUCT_VERSION

REPOSITORY = 'https://github.com/parm2006/Conduit'
API_URL = 'https://api.github.com/repos/parm2006/Conduit/releases?per_page=100'
MAX_INSTALLER_SIZE = 300 * 1024 * 1024
VERSION = re.compile(r'(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?')
logger = logging.getLogger(__name__)


def version_key(value):
    match = VERSION.fullmatch(value)
    if match is None:
        raise ValueError('Unsupported release version')
    major, minor, patch, stage, number = match.groups()
    return (int(major), int(minor), int(patch),
            {None: 3, 'rc': 2, 'beta': 1, 'alpha': 0}[stage], int(number or 0))


@dataclass(frozen=True)
class Update:
    version: str
    filename: str
    url: str
    checksum_url: str
    size: int


@dataclass(frozen=True)
class Download:
    path: Path
    sha256: str
    size: int


def select_update(releases, current=PRODUCT_VERSION):
    current_key = version_key(current)
    preview = '-' in current
    candidates = []
    for release in releases:
        try:
            if release.get('draft') or (release.get('prerelease') and not preview):
                continue
            version = release['tag_name'].removeprefix('v')
            key = version_key(version)
            if key <= current_key or ('-' in version and not preview):
                continue
            filename = f'Conduit-v{version}-Setup.exe'
            assets = {asset['name']: asset for asset in release['assets']}
            installer, checksums = assets[filename], assets['SHA256SUMS.txt']
            prefix = f'{REPOSITORY}/releases/download/v{version}/'
            if (installer['browser_download_url'] != prefix + filename or
                    checksums['browser_download_url'] != prefix + 'SHA256SUMS.txt'):
                continue
            size = installer['size']
            if not isinstance(size, int) or not 0 < size <= MAX_INSTALLER_SIZE:
                continue
            candidates.append((key, Update(version, filename, prefix + filename,
                                           prefix + 'SHA256SUMS.txt', size)))
        except (KeyError, TypeError, ValueError):
            continue
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def fetch_url(url):
    if urlsplit(url).scheme != 'https':
        raise ValueError('Updates require HTTPS')
    response = urlopen(Request(url, headers={
        'User-Agent': f'Conduit/{PRODUCT_VERSION}',
        'Accept': 'application/vnd.github+json' if url == API_URL else 'application/octet-stream',
    }), timeout=30)
    final = urlsplit(response.geturl())
    if final.scheme != 'https' or final.hostname not in (
            'api.github.com', 'github.com', 'release-assets.githubusercontent.com',
            'objects.githubusercontent.com'):
        response.close()
        raise ValueError('Unexpected update download destination')
    return response


def check_for_updates(current=PRODUCT_VERSION, *, fetch=fetch_url):
    with fetch(API_URL) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError('Release listing is too large')
    releases = json.loads(data)
    if not isinstance(releases, list):
        raise ValueError('Invalid release listing')
    return select_update(releases, current)


def download_update(update, directory=None, *, fetch=fetch_url):
    with fetch(update.checksum_url) as response:
        manifest = response.read(65537)
    if len(manifest) > 65536:
        raise ValueError('Checksum manifest is too large')
    digests = []
    for line in manifest.decode('utf-8-sig').splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip('*') == update.filename:
            if re.fullmatch('[0-9a-fA-F]{64}', fields[0]):
                digests.append(fields[0].lower())
    if len(digests) != 1:
        raise ValueError('Installer checksum is missing or ambiguous')
    base = Path(directory) if directory else Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Conduit' / 'updates'
    base.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='download-', dir=base))
    partial = stage / 'installer.part'
    try:
        digest, size = hashlib.sha256(), 0
        with fetch(update.url) as response, partial.open('xb') as output:
            while chunk := response.read(128 * 1024):
                size += len(chunk)
                if size > update.size or size > MAX_INSTALLER_SIZE:
                    raise ValueError('Installer exceeds expected size')
                digest.update(chunk)
                output.write(chunk)
        if size != update.size or digest.hexdigest() != digests[0]:
            raise ValueError('Installer verification failed')
        destination = stage / update.filename
        partial.replace(destination)
        return Download(destination, digests[0], size)
    except Exception:
        partial.unlink(missing_ok=True)
        stage.rmdir()
        raise


def verified_installer(download):
    with download.path.open('rb') as installer:
        digest = hashlib.file_digest(installer, 'sha256').hexdigest()
    if download.path.stat().st_size != download.size or digest != download.sha256:
        raise ValueError('Downloaded installer changed; download it again')
    return download.path


def can_install():
    """An updater must never overwrite a source checkout or portable launch."""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return False
    import win32com.shell.shell as shell
    from win32com.shell import shellcon
    expected = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAM_FILES, 0, 0)) / 'Conduit' / 'Conduit.exe'
    return Path(sys.executable).resolve() == expected.resolve()


def launch_installer(download):
    if not can_install():
        raise RuntimeError('Install Conduit with Setup to enable in-app installation')
    path = stage_installer(download)
    import win32api
    # Neither executable nor working directory may be writable by an unelevated process.
    process = subprocess.Popen([str(path)], cwd=win32api.GetSystemDirectory())
    # The app exits after handoff, so cleanup cannot depend on one of its threads.
    # Keep the installer available for setup, then remove it at the next reboot.
    import win32file
    import win32con
    try:
        win32file.MoveFileEx(str(path), None, win32con.MOVEFILE_DELAY_UNTIL_REBOOT)
        win32file.MoveFileEx(str(path.parent), None, win32con.MOVEFILE_DELAY_UNTIL_REBOOT)
    except OSError:
        logger.warning('Protected update staging could not be scheduled for cleanup')
    return process


def stage_installer(download):
    """Copy into an admin-owned directory, verifying the copied bytes.

    Explicit ownership matters: an elevated user's normal SID must not retain
    implicit WRITE_DAC ownership rights when running without elevation later.
    """
    import win32con
    import win32file
    import win32security
    import pywintypes
    import ntsecuritycon
    from win32com.shell import shell, shellcon

    if download.path.name != Path(download.path.name).name or not re.fullmatch(
            r'Conduit-v\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?-Setup\.exe', download.path.name):
        raise ValueError('Invalid installer filename')
    administrators = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid)
    system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)
    acl = win32security.ACL()
    for sid in (administrators, system):
        acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, 3, ntsecuritycon.FILE_ALL_ACCESS, sid)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(administrators, False)
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    descriptor.SetSecurityDescriptorControl(win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED)
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    # Program Files prevents a medium-integrity process replacing a parent directory.
    base = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAM_FILES, 0, 0))
    directory = base / f'Conduit-Update-{uuid.uuid4().hex}'
    win32file.CreateDirectory(str(directory), attributes)
    destination = directory / download.path.name
    try:
        handle = win32file.CreateFile(str(destination), win32con.GENERIC_WRITE, 0,
                                     attributes, win32con.CREATE_NEW, win32con.FILE_ATTRIBUTE_NORMAL, None)
        digest, size = hashlib.sha256(), 0
        try:
            with download.path.open('rb') as source:
                while chunk := source.read(128 * 1024):
                    size += len(chunk)
                    if size > download.size:
                        raise ValueError('Installer changed during staging')
                    digest.update(chunk)
                    win32file.WriteFile(handle, chunk)
        finally:
            handle.Close()
        if size != download.size or digest.hexdigest() != download.sha256:
            raise ValueError('Installer changed during staging')
        verified_installer(Download(destination, download.sha256, download.size))
        return destination
    except Exception:
        destination.unlink(missing_ok=True)
        directory.rmdir()
        raise

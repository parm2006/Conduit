"""Trace large-file manifest preparation under pdb without touching the clipboard.

This exercises the real snapshot/hash, handshake, and FilePasteService code. It
uses in-process control callbacks and a recording publisher, not real Explorer.
"""
import argparse
from contextlib import contextmanager
import hashlib
import inspect
from pathlib import Path
import pdb
import sys
import tempfile
import time
from unittest.mock import patch

from app.file_transfer.handshake import ManifestHandshakeQueue
from app.file_transfer.paste_service import FilePasteService
from app.file_transfer.selection import snapshot_selection
from app.file_transfer.source import SourceFile
from app.file_transfer.receiver import TransferReceiver
from app.file_transfer.sender import TransferSender


class RecordingReceiver:
    def __init__(self): self.manifests = []
    def accept_manifest(self, manifest): self.manifests.append(manifest)


class RecordingPublisher:
    has_pending_paste = False
    def __init__(self): self.jobs = []
    def publish_and_paste(self, manifest, receiver): self.jobs.append(manifest['job_id'])


class RecordingExecutor:
    def __init__(self): self.jobs = []; self.submissions = []
    def submit(self, manifest, sources):
        self.jobs.append(manifest.job_id)
        self.submissions.append((manifest, sources))


class InProcessFileLane:
    """Dispatch real file-lane messages without opening a network connection."""
    supports_chunk_ack = True

    def __init__(self): self.callbacks = {}; self.chunks = 0
    def register_callback(self, kind, callback):
        self.callbacks.setdefault(kind, []).append(callback)
    def send(self, metadata, payload=b''):
        if metadata['type'] == 'chunk': self.chunks += 1
        for callback in self.callbacks.get(metadata['type'], ()):
            callback(metadata, payload)
        return True


def verify_transfer(receiver, executor):
    lane = InProcessFileLane()
    receiver.attach(lane)
    sender = TransferSender(lane)
    manifest, sources = executor.submissions[0]
    started = time.monotonic()
    sender.send_job(manifest, sources, announce_manifest=False)
    for relative_path, source in sources.items():
        digest = hashlib.sha256()
        offset = 0
        while offset < source.size:
            data = receiver.read_range(manifest.job_id, relative_path, offset, 1024 * 1024)
            if not data:
                raise RuntimeError('Destination returned EOF before the declared size')
            digest.update(data)
            offset += len(data)
        if digest.hexdigest() != source.sha256:
            raise RuntimeError('Destination SHA-256 mismatch')
        print('Destination verified bytes:', offset)
    print('File chunks delivered and acknowledged:', lane.chunks)
    print('Transfer and decrypted readback seconds:', round(time.monotonic() - started, 4))
    print('Destination SHA-256: MATCH')


class Control:
    def __init__(self): self.messages = []; self.dispatch = None
    def send_message(self, message):
        self.messages.append(message)
        return True if self.dispatch is None else self.dispatch(message)


class ManifestDebugger(pdb.Pdb):
    def __init__(self):
        super().__init__(stdout=sys.stdout, nosigint=True)
        self._wait_for_mainpyfile = False
        for function, marker in ((SourceFile.snapshot, 'resolved = Path(path)'),
                (FilePasteService.on_manifest_response, 'request_id = message.get'),
                (ManifestHandshakeQueue.accept, 'request = self._by_id.get')):
            lines, first = inspect.getsourcelines(function)
            offset = next(i for i, line in enumerate(lines) if marker in line)
            self.set_break(inspect.getsourcefile(function), first + offset)

    def user_line(self, frame):
        function = frame.f_code.co_name
        print('PDB:', Path(frame.f_code.co_filename).name, function, 'line', frame.f_lineno)
        owner = frame.f_locals.get('self')
        if isinstance(owner, ManifestHandshakeQueue):
            print('  pending lookup entries:', len(owner._by_id))
            if owner._requests:
                request = owner._requests[-1]
                print('  request state:', request.state.value)
                print('  seconds past deadline:', round(owner.clock() - request.deadline, 4))
        self.set_continue()


@contextmanager
def delayed_reads(path, milliseconds):
    if not milliseconds:
        yield
        return
    original = Path.open
    class Reader:
        def __init__(self, file): self.file = file
        def __getattr__(self, name): return getattr(self.file, name)
        def __enter__(self): return self
        def __exit__(self, *args): self.file.close()
        def read(self, count=-1):
            time.sleep(milliseconds / 1000)
            return self.file.read(count)
    def open_file(candidate, *args, **kwargs):
        file = original(candidate, *args, **kwargs)
        return Reader(file) if candidate == path and args and args[0] == 'rb' else file
    with patch.object(Path, 'open', open_file):
        yield


def run(size_mib=500, read_delay_ms=0, transfer=False):
    if not 1 <= size_mib <= 5120 or not 0 <= read_delay_ms <= 10:
        raise ValueError('Use 1..5120 MiB and 0..10 ms per read')
    root = (Path(__file__).resolve().parents[1] / 'build/file-paste-debug').resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='manifest-', dir=root) as directory:
        assert Path(directory).resolve().is_relative_to(root)
        path = Path(directory) / 'debug-large.bin'
        with path.open('wb') as file:
            file.truncate(size_mib * 1024 * 1024)
        destination_control, source_control = Control(), Control()
        receiver = TransferReceiver(Path(directory) / 'cache') if transfer else RecordingReceiver()
        publisher = RecordingPublisher()
        destination = FilePasteService(destination_control, receiver, publisher, None,
            lambda: None, executor=RecordingExecutor())
        source = FilePasteService(source_control, RecordingReceiver(), RecordingPublisher(), None,
            lambda: snapshot_selection([path]), executor=RecordingExecutor())
        source_control.dispatch = lambda message: destination.on_manifest_response(message) if message['type'] == 'file_manifest_response' else destination.on_manifest_failed(message)
        destination_control.dispatch = lambda message: source.on_manifest_ack(message) if message['type'] == 'file_manifest_ack' else True
        request = destination.request_paste()
        start = time.monotonic()
        print('Fixture bytes:', path.stat().st_size)
        print('Manifest deadline seconds:', destination.handshakes.timeout_seconds)
        print('Injected per-read delay ms:', read_delay_ms)
        try:
            with delayed_reads(path, read_delay_ms):
                ManifestDebugger().runcall(source.on_manifest_request, {'request_id': request.request_id})
            print('Preparation and acceptance seconds:', round(time.monotonic() - start, 4))
            print('Final request state:', request.state.value)
            print('Manifests received:', len(receiver._jobs) if transfer else len(receiver.manifests))
            print('Publisher invoked:', len(publisher.jobs))
            print('Transfer jobs started:', len(source.executor.jobs))
            if transfer and publisher.jobs:
                verify_transfer(receiver, source.executor)
            return bool(publisher.jobs)
        finally:
            if transfer:
                receiver.cancel_all('Debugger fixture cleanup')
            for service in (source, destination):
                for timer in tuple(service._outgoing_timers.values()) + tuple(service.handshakes._timers.values()):
                    timer.cancel()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size-mib', type=int, default=500)
    parser.add_argument('--read-delay-ms', type=float, default=0)
    parser.add_argument('--transfer', action='store_true',
        help='Also send bounded chunks, stage encrypted data, and verify decrypted SHA-256')
    args = parser.parse_args()
    return 0 if run(args.size_mib, args.read_delay_ms, args.transfer) else 1


if __name__ == '__main__':
    raise SystemExit(main())

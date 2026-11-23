import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from blake3 import blake3
from app.file_transfer.models import FileItem, ItemType, Manifest
from app.file_transfer.source import SourceFile
from app.file_transfer.selection import snapshot_selection
from app.file_transfer.staging import IntegrityError, StagedFile
from app.file_transfer.paste_service import FilePasteService
from app.file_transfer.sender import TransferSender
from app.file_transfer.receiver import TransferReceiver
from app.file_transfer.controller import TransferController
from app.file_transfer.toast import toast_view
from tests.test_file_paste_service import (RecordingControl, RecordingPublisher,
    RecordingReceiver, RecordingSender, ImmediateExecutor, VALID_REQUEST)
from tests.test_file_transfer_sender import LoopbackLane
from tests import test_cluster_file_routing as routing_tests


class FileHashingTests(unittest.TestCase):
    def make_service(self, snapshot):
        sender = RecordingSender()
        service = FilePasteService(RecordingControl(), RecordingReceiver(),
            RecordingPublisher(), sender, snapshot, executor=ImmediateExecutor(sender))
        self.addCleanup(lambda: [timer.cancel() for timer in
            tuple(service._outgoing_timers.values()) + tuple(service.handshakes._timers.values())])
        return service

    def test_blake3_source_matches_reference_for_random_and_empty_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'random.bin'
            for content in (os.urandom(2 * 1024 * 1024 + 17), b''):
                path.write_bytes(content)
                source = SourceFile.snapshot(path, hash_algorithm='blake3')
                self.assertEqual(source.digest, blake3(content).hexdigest())
                self.assertEqual(source.hash_algorithm, 'blake3')
                self.assertEqual(b''.join(source.iter_chunks()), content)

    def test_blake3_mapped_source_rejects_file_open_for_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'busy.bin'
            path.write_bytes(os.urandom(4096))
            with path.open('r+b'):
                with self.assertRaises(OSError):
                    SourceFile.snapshot(path, hash_algorithm='blake3')

    def test_blake3_selection_has_explicit_algorithm_and_generic_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'random.bin'
            path.write_bytes(os.urandom(1024))
            manifest, sources = snapshot_selection([path], hash_algorithm='blake3')
            wire = manifest.to_wire()
            self.assertEqual(wire['hash_algorithm'], 'blake3')
            self.assertEqual(wire['items'][0]['digest'], sources[path.name].digest)
            self.assertNotIn('sha256', wire['items'][0])
            self.assertEqual(Manifest.from_wire(wire), manifest)

    def test_legacy_sha256_wire_format_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.bin'
            path.write_bytes(b'legacy')
            manifest, sources = snapshot_selection([path])
            wire = manifest.to_wire()
            self.assertNotIn('hash_algorithm', wire)
            self.assertEqual(wire['items'][0]['sha256'], hashlib.sha256(b'legacy').hexdigest())
            self.assertNotIn('digest', wire['items'][0])

    def test_unknown_or_ambiguous_wire_algorithm_is_rejected(self):
        wire = Manifest.create([FileItem('x', ItemType.FILE, 1, 0, 'a' * 64)]).to_wire()
        for algorithm in ('xxh3', None, [], 'BLAKE3'):
            with self.subTest(algorithm=algorithm), self.assertRaises(ValueError):
                Manifest.from_wire(dict(wire, hash_algorithm=algorithm))
        wire['hash_algorithm'] = 'blake3'
        wire['items'][0]['digest'] = 'b' * 64
        with self.assertRaises(ValueError):
            Manifest.from_wire(wire)

    def test_request_advertises_supported_hashes_and_uses_three_second_deadline(self):
        service = self.make_service(lambda **kw: None)
        service.request_paste()
        self.assertEqual(service.control.messages[0]['supported_hash_algorithms'], ['blake3', 'sha256'])
        self.assertEqual(service.handshakes.timeout_seconds, 3.0)

    def test_new_peers_choose_blake3_and_legacy_requests_choose_sha256(self):
        for capabilities, expected in ((['blake3', 'sha256'], 'blake3'),
                                       (['sha256'], 'sha256'), (None, 'sha256')):
            with self.subTest(capabilities=capabilities):
                selected = []
                def snapshot(hash_algorithm='sha256'):
                    selected.append(hash_algorithm)
                    return Manifest.create([FileItem('x', ItemType.FILE, 1, 0, 'a' * 64)],
                                           hash_algorithm=hash_algorithm), {}
                service = self.make_service(snapshot)
                message = {'request_id': VALID_REQUEST}
                if capabilities is not None:
                    message['supported_hash_algorithms'] = capabilities
                self.assertTrue(service.on_manifest_request(message))
                self.assertEqual(selected, [expected])

    def test_unsupported_negotiation_fails_before_hashing(self):
        called = []
        service = self.make_service(lambda **kw: called.append(True))
        self.assertFalse(service.on_manifest_request({'request_id': VALID_REQUEST,
            'supported_hash_algorithms': ['xxh3']}))
        self.assertEqual(called, [])
        self.assertEqual(service.control.messages[-1]['error'], 'UnsupportedHashAlgorithm')

    def test_unknown_response_fails_request_without_ack_or_paste(self):
        service = self.make_service(lambda **kw: None)
        request = service.request_paste()
        self.assertFalse(service.on_manifest_response({'request_id': request.request_id,
            'manifest': {'job_id': 'a' * 32, 'hash_algorithm': 'xxh3'}}))
        self.assertEqual(request.error, 'UnsupportedHashAlgorithm')
        self.assertEqual(service.publisher.jobs, [])
        self.assertFalse(any(m['type'] == 'file_manifest_ack' for m in service.control.messages))

    def test_blake3_transfer_stages_and_verifies_random_data(self):
        for algorithm in ('sha256', 'blake3'):
            with self.subTest(algorithm=algorithm), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'random.bin'
                content = os.urandom(1024 * 1024 + 71)
                path.write_bytes(content)
                manifest, sources = snapshot_selection([path], hash_algorithm=algorithm)
                receiver = TransferReceiver(Path(directory) / 'cache')
                try:
                    TransferSender(LoopbackLane(receiver)).send_job(manifest, sources)
                    self.assertEqual(receiver.read_range(manifest.job_id, path.name, 0, len(content)), content)
                finally:
                    receiver.cancel_all('test cleanup')

    def test_blake3_staging_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            staged = StagedFile(Path(directory), 'a' * 32, 'x', 4,
                blake3(b'good').hexdigest(), hash_algorithm='blake3')
            staged.write(0, b'evil')
            with self.assertRaises(IntegrityError):
                staged.finalize()

    def test_unknown_staging_algorithm_creates_no_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                StagedFile(Path(directory), 'a' * 32, 'x', 4, 'a' * 64,
                           hash_algorithm='xxh3')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_preparation_timeout_is_visible_and_sent_once(self):
        service = self.make_service(lambda **kw: None)
        service.receiver.controller = TransferController()
        clock = SimpleNamespace(now=100.)
        service.handshakes.clock = lambda: clock.now
        class Timer:
            def start(self): pass
            def cancel(self): pass
        service.handshakes.timer_factory = lambda delay, callback: Timer()
        request = service.request_paste()
        clock.now += 3.01
        service.handshakes.expire()
        service.handshakes.expire()
        status = service.receiver.controller.status(request.request_id)
        self.assertEqual(status.error_code, 'ManifestPreparationTimeout')
        self.assertIn('3 seconds', toast_view(status).details)
        failures = [m for m in service.control.messages if m['type']=='file_manifest_failed']
        self.assertEqual(len(failures), 1)
        self.assertFalse(service.on_manifest_response({'request_id': request.request_id,
            'manifest': {'job_id': 'a' * 32}}))
        self.assertEqual(service.publisher.jobs, [])

    def test_mapper_holds_deny_write_handle_until_hash_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'random.bin'
            content = os.urandom(1024 * 1024)
            path.write_bytes(content)
            owner = self
            class CheckedHasher:
                AUTO = blake3.AUTO
                def __init__(self, **kwargs): self.hasher = blake3(**kwargs)
                def update_mmap(self, filename):
                    with owner.assertRaises(OSError):
                        Path(filename).write_bytes(b'truncated')
                    self.hasher.update_mmap(filename)
                def hexdigest(self): return self.hasher.hexdigest()
            with patch('app.file_transfer.source.blake3', CheckedHasher):
                source = SourceFile.snapshot(path, hash_algorithm='blake3')
            self.assertEqual(source.digest, blake3(content).hexdigest())

    def test_sender_rejects_algorithm_mismatch_even_when_digest_text_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'x'
            path.write_bytes(os.urandom(4096))
            manifest, sources = snapshot_selection([path], hash_algorithm='sha256')
            wrong = Manifest(manifest.job_id, manifest.items, manifest.total_size,
                             manifest.file_count, hash_algorithm='blake3')
            receiver = TransferReceiver(Path(directory) / 'cache')
            try:
                with self.assertRaisesRegex(ValueError, 'source snapshot'):
                    TransferSender(LoopbackLane(receiver)).send_job(wrong, sources)
            finally:
                receiver.cancel_all('test cleanup')


class HashRoutingTests(unittest.TestCase):
    def test_both_algorithms_route_all_six_endpoint_pairs(self):
        for algorithm in ('sha256', 'blake3'):
            for source in routing_tests.MACHINES:
                for destination in routing_tests.MACHINES:
                    if source == destination:
                        continue
                    with self.subTest(algorithm=algorithm, source=source, destination=destination):
                        fixture = routing_tests.ClusterFileRouterTests()
                        fixture.setUp()
                        fixture.offer = routing_tests.ClipboardHubItem(5, source, 17, 'files')
                        job = fixture.router.request_paste(destination, VALID_REQUEST,
                            supported_hash_algorithms=['blake3', 'sha256'])
                        manifest = Manifest.create([FileItem('x', ItemType.FILE, 4, 0, 'a' * 64)],
                                                   hash_algorithm=algorithm)
                        self.assertTrue(fixture.router.on_manifest_response(source, {
                            'request_id': VALID_REQUEST, 'manifest': manifest.to_wire()}))
                        self.assertEqual(fixture.control[-1][0], destination)
                        self.assertEqual(Manifest.from_wire(fixture.control[-1][1]['manifest']), manifest)
                        self.assertTrue(fixture.router.on_manifest_ack(destination, {'job_id': manifest.job_id}))
                        self.assertTrue(fixture.router.relay_frame(source, {
                            'type': 'chunk', 'job_id': manifest.job_id,
                            'relative_path': 'x', 'offset': 0}, b'data'))
                        self.assertEqual(fixture.frames[-1][0], destination)

    def test_cluster_router_preserves_destination_capabilities(self):
        fixture = routing_tests.ClusterFileRouterTests()
        fixture.setUp()
        job = fixture.router.request_paste('client-2', VALID_REQUEST,
            supported_hash_algorithms=['blake3', 'sha256'])
        self.assertIsNotNone(job)
        self.assertEqual(fixture.control[-1][1]['supported_hash_algorithms'], ['blake3', 'sha256'])

    def test_destination_timeout_releases_router_capacity(self):
        fixture = routing_tests.ClusterFileRouterTests()
        fixture.setUp()
        job = fixture.router.request_paste('client-2', VALID_REQUEST)
        self.assertFalse(fixture.router.on_manifest_failed('server', {
            'request_id': VALID_REQUEST, 'error': 'ManifestPreparationTimeout'}))
        self.assertTrue(fixture.router.on_manifest_failed('client-2', {
            'request_id': VALID_REQUEST, 'error': 'ManifestPreparationTimeout'}))
        self.assertIsNone(fixture.router.job(job.job_id))


if __name__ == '__main__':
    unittest.main()

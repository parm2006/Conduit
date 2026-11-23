import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.file_transfer.paste_service import FilePasteService
from tests.test_file_paste_service import (RecordingControl, RecordingReceiver,
    RecordingPublisher, RecordingSender, ImmediateExecutor, Manifest, JOB_A, VALID_REQUEST)

class NoTimer:
    def start(self): pass
    def cancel(self): pass

class LargeFilePreparationTests(unittest.TestCase):
    def service(self, snapshot):
        sender = RecordingSender()
        service = FilePasteService(RecordingControl(), RecordingReceiver(), RecordingPublisher(),
            sender, lambda **kw: snapshot(), executor=ImmediateExecutor(sender))
        self.addCleanup(lambda: [timer.cancel() for timer in tuple(service._outgoing_timers.values())])
        return service

    def wait_idle(self, service):
        deadline = time.monotonic() + 2
        while service._pending_snapshots and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(service._pending_snapshots, 0)

    def test_manifest_preparation_over_one_second_is_accepted(self):
        clock = SimpleNamespace(now=100.)
        service = self.service(lambda: None)
        service.handshakes.clock = lambda: clock.now
        service.handshakes.timer_factory = lambda delay, fn: NoTimer()
        request = service.request_paste()
        clock.now += 1.5
        self.assertTrue(service.on_manifest_response({'request_id': request.request_id,
                                                      'manifest': Manifest(JOB_A).to_wire()}))
        self.assertEqual(service.publisher.jobs, [JOB_A])
        self.assertEqual(service.handshakes.timeout_seconds, 3.)

    def test_hashing_runs_off_control_thread_and_still_sends_manifest(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def snapshot():
            entered.set(); release.wait(2)
            return Manifest(JOB_A), {}
        service = self.service(snapshot)
        self.assertTrue(service.queue_manifest_request({'request_id': VALID_REQUEST}))
        self.assertTrue(entered.wait(.5))
        self.assertEqual(service.control.messages, [])
        release.set(); self.wait_idle(service)
        self.assertEqual(service.control.messages[0]['type'], 'file_manifest_response')

    def test_preparation_slots_are_reserved_before_starting_workers(self):
        release = threading.Event(); self.addCleanup(release.set)
        def snapshot():
            release.wait(2)
            raise OSError('test preparation failure')
        service = self.service(snapshot)
        for number in range(8):
            self.assertTrue(service.queue_manifest_request({'request_id': f'{number + 1:032x}'}))
        self.assertFalse(service.queue_manifest_request({'request_id': 'f' * 32}))
        self.assertEqual(service._pending_snapshots, 8)
        release.set(); self.wait_idle(service)

    def test_reconnect_discards_old_preparation_result(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def snapshot():
            entered.set(); release.wait(2)
            return Manifest(JOB_A), {}
        service = self.service(snapshot)
        service.control._generation = 1; service.control.connected = True
        service.queue_manifest_request({'request_id': VALID_REQUEST})
        self.assertTrue(entered.wait(.5))
        service.control._generation = 2
        release.set(); self.wait_idle(service)
        self.assertEqual(service.control.messages, [])
        self.assertEqual(service._outgoing, {})

    def test_production_client_and_local_server_queue_preparation(self):
        from app.client import ConduitClient
        from app.server import ConduitServer
        client = ConduitClient.__new__(ConduitClient)
        client.file_paste_service = Mock()
        client.on_file_manifest_request({'request_id': VALID_REQUEST})
        client.file_paste_service.queue_manifest_request.assert_called_once()
        client.file_paste_service.on_manifest_request.assert_not_called()
        server = ConduitServer.__new__(ConduitServer)
        server.file_paste_service = Mock(); server.cluster_file_router = None
        server.on_file_manifest_request({'request_id': VALID_REQUEST})
        server.file_paste_service.queue_manifest_request.assert_called_once()
        server._dispatch_local_file_control({'type': 'file_manifest_request', 'request_id': VALID_REQUEST})
        self.assertEqual(server.file_paste_service.queue_manifest_request.call_count, 2)

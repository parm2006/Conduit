import threading
import unittest
from unittest.mock import patch
from app.file_transfer.controller import TransferController
from app.file_transfer.toast import toast_view
from tests import test_file_paste_service as fixtures
from tests.test_file_paste_service import Manifest, JOB_A, VALID_REQUEST


class FilePreparationDeadlineTests(unittest.TestCase):
    def test_source_retries_a_transient_clipboard_lock_without_another_paste(self):
        from app.clipboard_handler import ClipboardHandler
        clipboard = ClipboardHandler.__new__(ClipboardHandler)
        clipboard._file_drop_format = 15
        with patch('app.clipboard_handler.win32clipboard.OpenClipboard', side_effect=[OSError('busy'), None]) as opened, \
                patch('app.clipboard_handler.win32clipboard.CloseClipboard') as closed, \
                patch('app.clipboard_handler.win32clipboard.IsClipboardFormatAvailable', return_value=True), \
                patch('app.clipboard_handler.win32clipboard.GetClipboardData', return_value=('file.txt',)), \
                patch('app.clipboard_handler.time.sleep'):
            self.assertEqual(clipboard.read_file_selection(), ('file.txt',))
        self.assertEqual(opened.call_count, 2)
        closed.assert_called_once_with()

    def service(self):
        service, control, receiver, publisher, sender = fixtures.FilePasteServiceTests().make_service([])
        receiver.controller = TransferController()
        return service, control, receiver, publisher

    def test_preparation_waits_five_seconds_and_shows_failure_once(self):
        service, control, receiver, publisher = self.service()
        now = [100.0]
        service.handshakes.clock = lambda: now[0]
        request = service.request_paste()
        self.assertEqual(request.deadline, 105.0)
        now[0] = 101.27
        self.assertEqual(service.handshakes.expire(), [])
        now[0] = 105.01
        self.assertEqual(service.handshakes.expire(), [request])
        status = receiver.controller.status(request.request_id)
        self.assertIn('Failed to hash in 5 seconds', toast_view(status).details)
        self.assertFalse(service.on_manifest_response({'request_id':request.request_id,'manifest':{'job_id':JOB_A}}))
        self.assertEqual(publisher.jobs, [])
        self.assertEqual(service.handshakes.expire(), [])
        self.assertFalse(service.destination_paste_active)

    def test_source_hashing_does_not_block_the_control_callback(self):
        service, control, _, _ = self.service()
        release, finished = threading.Event(), threading.Event()
        def snapshot():
            release.wait(2)
            return Manifest(JOB_A), {}
        service.snapshot_selection = snapshot
        send = control.send_message
        def response(message):
            result = send(message)
            finished.set()
            return result
        control.send_message = response
        try:
            self.assertTrue(service.queue_manifest_request({'request_id': VALID_REQUEST}))
            self.assertFalse(finished.is_set())
            self.assertEqual(service._pending_snapshots, 1)
        finally:
            release.set()
        self.assertTrue(finished.wait(2))

    def test_source_discards_preparation_completed_after_five_seconds(self):
        service, control, _, _ = self.service()
        now = [0.0]
        service._clock = lambda: now[0]
        def slow_snapshot():
            now[0] = 5.01
            return Manifest(JOB_A), {}
        service.snapshot_selection = slow_snapshot
        self.assertFalse(service.on_manifest_request({'request_id': VALID_REQUEST}))
        self.assertEqual(service._outgoing, {})
        self.assertEqual(service._pending_snapshots, 0)
        self.assertEqual(control.messages[-1]['error'], 'ManifestPreparationTimeout')

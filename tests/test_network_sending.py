import threading
import time
import unittest

from app.network import MAX_MESSAGE_SIZE, NetworkNode


class OverlapDetectingSocket:
    def __init__(self):
        self.active = 0
        self.overlap = False
        self.started = threading.Event()
        self.release = threading.Event()

    def sendall(self, data):
        self.active += 1
        if self.active > 1:
            self.overlap = True
        self.started.set()
        self.release.wait(1)
        self.active -= 1


class NetworkSendingTests(unittest.TestCase):
    def test_disconnect_timeout_survives_blocked_send(self):
        # Exercise both a heartbeat stuck in sendall and a heartbeat waiting
        # behind an application send. Only socket shutdown releases the writer.
        for application_send in (False, True):
            with self.subTest(application_send=application_send):
                class StalledSocket:
                    def __init__(self):
                        self.started = threading.Event()
                        self.closed = threading.Event()

                    def sendall(self, data):
                        self.started.set()
                        self.closed.wait()
                        raise OSError("socket shut down")

                    def shutdown(self, how):
                        self.closed.set()

                    def close(self):
                        self.closed.set()

                sock = StalledSocket()
                node = NetworkNode(heartbeat_interval=0.02, heartbeat_timeout=0.1)
                disconnected = threading.Event()
                callbacks = []
                def on_disconnect(data):
                    callbacks.append(data)
                    disconnected.set()
                node.register_callback("disconnected", on_disconnect)
                writer = None
                try:
                    node._attach_socket(sock)
                    if application_send:
                        writer = threading.Thread(
                            target=lambda: node.send_message({"type": "clipboard_sync"}),
                            daemon=True,
                        )
                        writer.start()
                    self.assertTrue(sock.started.wait(1))
                    self.assertTrue(
                        disconnected.wait(1),
                        "Blocked send prevented the disconnect watchdog from expiring",
                    )
                    self.assertFalse(node.connected)
                    self.assertTrue(sock.closed.is_set())
                    node._heartbeat_thread.join(1)
                    node._watchdog_thread.join(1)
                    self.assertFalse(node._heartbeat_thread.is_alive())
                    self.assertFalse(node._watchdog_thread.is_alive())
                    if writer is not None:
                        writer.join(1)
                        self.assertFalse(writer.is_alive())
                    self.assertEqual(len(callbacks), 1)
                finally:
                    node.disconnect()
                    sock.closed.set()
                    if writer is not None:
                        writer.join(1)

    def test_heartbeat_disconnects_when_peer_stops_responding(self):
        class Socket:
            def __init__(self):
                self.sent = []

            def sendall(self, data):
                self.sent.append(data)

            def shutdown(self, how):
                pass

            def close(self):
                pass

        disconnected = threading.Event()
        node = NetworkNode(heartbeat_interval=0.01, heartbeat_timeout=0.03)
        node.register_callback("disconnected", lambda data: disconnected.set())
        node._attach_socket(Socket())

        self.assertTrue(disconnected.wait(0.5))
        self.assertFalse(node.connected)

    def test_oversized_local_message_is_rejected_without_disconnect(self):
        class Socket:
            def __init__(self):
                self.sent = []

            def sendall(self, data):
                self.sent.append(data)

        node = NetworkNode()
        node.connected = True
        node.authenticated = True
        node.sock = Socket()
        original_socket = node.sock

        with self.assertLogs("app.network", level="ERROR"):
            self.assertFalse(node.send_message({
                "type": "clipboard_sync",
                "text": "x" * (MAX_MESSAGE_SIZE + 1),
            }))

        self.assertTrue(node.connected)
        self.assertTrue(node.authenticated)
        self.assertIs(node.sock, original_socket)
        self.assertEqual(original_socket.sent, [])

    def test_concurrent_tls_messages_are_serialized(self):
        node = NetworkNode()
        node.connected = True
        node.sock = OverlapDetectingSocket()
        first = threading.Thread(target=lambda: node.send_message({"type": "first"}))
        second = threading.Thread(target=lambda: node.send_message({"type": "second"}))

        first.start()
        self.assertTrue(node.sock.started.wait(1))
        second.start()
        time.sleep(0.02)

        self.assertFalse(node.sock.overlap)
        node.sock.release.set()
        first.join(1)
        second.join(1)


if __name__ == "__main__":
    unittest.main()

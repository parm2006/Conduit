import unittest
from app.mouse_datagrams import MouseSender, MouseReceiver

class MouseDatagramTests(unittest.TestCase):
    def pair(self):
        delivered = []
        receiver = MouseReceiver('127.0.0.1', 0, lambda m: delivered.append(m))
        sender = MouseSender('127.0.0.1', receiver.port, receiver.key)
        self.addCleanup(receiver.close)
        self.addCleanup(sender.close)
        return sender, receiver, delivered

    def test_loss_reordering_and_authentication(self):
        sender, receiver, delivered = self.pair()
        first = sender.packet(10, 20)
        second = sender.packet(30, 40)
        self.assertTrue(receiver.accept(second))
        self.assertFalse(receiver.accept(first))
        self.assertFalse(receiver.accept(second))
        corrupt = bytearray(sender.packet(50, 60)); corrupt[-1] ^= 1
        self.assertFalse(receiver.accept(bytes(corrupt)))
        self.assertEqual(delivered, [{'type': 'mouse_position', 'x': 30, 'y': 40}])

    def test_click_barrier_repairs_lost_position_and_rejects_late_packets(self):
        sender, receiver, delivered = self.pair()
        late = sender.packet(10, 20)
        click = sender.reliable({'type': 'mouse_click', 'pressed': True})
        future = sender.packet(30, 40)
        self.assertFalse(receiver.accept(future))
        receiver.dispatch(click, lambda m: delivered.append(m))
        self.assertFalse(receiver.accept(late))
        self.assertTrue(receiver.accept(future))
        self.assertEqual([m['type'] for m in delivered], ['mouse_position', 'mouse_click', 'mouse_position'])
        self.assertEqual(delivered[0]['x'], 10)

    def test_old_session_key_and_switch_discard_previous_position(self):
        sender, receiver, delivered = self.pair()
        other, _, _ = self.pair()
        self.assertFalse(receiver.accept(other.packet(99, 99)))
        sender.packet(10, 20)
        switch = sender.reliable({'type': 'switch'})
        receiver.dispatch(switch, delivered.append)
        self.assertEqual(delivered, [switch])

    def test_unconfirmed_path_uses_reliable_fallback(self):
        sender, _, _ = self.pair()
        self.assertFalse(sender.send_position(1, 2))

    def test_loopback_probe_confirms_path_and_position_delivery(self):
        import time
        sender, receiver, delivered = self.pair()
        receiver.start(); sender.start()
        deadline = time.monotonic() + 2
        while not sender.ready and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(sender.ready)
        self.assertTrue(sender.send_position(12, 34))
        deadline = time.monotonic() + 2
        while not delivered and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(delivered[0]['x'], 12)

    def test_fallback_barrier_prevents_udp_overtaking(self):
        sender, receiver, delivered = self.pair()
        receiver.accept(sender.packet(3, 0))
        fallback = sender.reliable({'type': 'mouse_position', 'x': 4, 'y': 0})
        future = sender.packet(5, 0)
        self.assertFalse(receiver.accept(future))
        receiver.dispatch(fallback, delivered.append)
        self.assertTrue(receiver.accept(future))
        self.assertEqual([m['x'] for m in delivered], [3, 4, 5])

    def test_heartbeat_does_not_ack_undelivered_movement(self):
        sender, receiver, delivered = self.pair()
        packet = sender.packet(3, 4)
        receiver.dispatch(sender.reliable({'type': '__conduit_heartbeat__'}), lambda m: None)
        self.assertTrue(receiver.accept(packet))


    def test_lost_final_position_is_repeated_without_new_input(self):
        import time
        sender, receiver, delivered = self.pair()
        receiver.start(); sender.start()
        deadline = time.monotonic() + 2
        while not sender.ready and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(sender.ready)
        from unittest.mock import patch
        original = receiver.accept
        dropped = []
        def drop_first(packet):
            if not dropped:
                dropped.append(True)
                return False
            return original(packet)
        with patch.object(receiver, 'accept', side_effect=drop_first):
            self.assertTrue(sender.send_position(12, 34))
            deadline = time.monotonic() + 2
            while not delivered and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertTrue(dropped)
        self.assertEqual(delivered[0]['x'], 12)

    def test_relative_transition_repairs_lost_absolute_origin(self):
        sender, receiver, delivered = self.pair()
        sender.packet(100, 100)
        receiver.dispatch(sender.reliable({'type': 'mouse_move', 'dx': 5, 'dy': 0}), delivered.append)
        self.assertEqual(delivered[0], {'type': 'mouse_position', 'x': 100, 'y': 100})
        self.assertEqual((delivered[1]['type'], delivered[1]['dx'], delivered[1]['dy']), ('mouse_move', 5, 0))


    def test_relative_batch_preserves_path_and_repairs_absolute_origin(self):
        sender, receiver, delivered = self.pair()
        sender.packet(100, 100)
        batch = {'type': 'mouse_move_batch', 'deltas': [[10, 0], [-10, 0]]}
        receiver.dispatch(sender.reliable(batch), delivered.append)
        self.assertEqual(delivered[0], {'type': 'mouse_position', 'x': 100, 'y': 100})
        self.assertEqual(delivered[1]['deltas'], [[10, 0], [-10, 0]])
        self.assertIsNone(sender.position)


class MouseNetworkIntegrationTests(unittest.TestCase):
    def test_negotiation_fallback_barrier_and_disconnect(self):
        import socket
        import threading
        import time
        from types import SimpleNamespace
        from app.network import NetworkNode
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0)); listener.listen()
        client_socket = socket.create_connection(listener.getsockname())
        server_socket, _ = listener.accept(); listener.close()
        server, client = NetworkNode(), NetworkNode()
        server.owner = SimpleNamespace(role='control')
        client.role = 'control'; client.is_server = False
        client.port = server_socket.getsockname()[1]
        received = []
        for kind in ('mouse_position', 'mouse_move', 'mouse_click'):
            client.register_callback(kind, received.append)
        self.addCleanup(client.disconnect); self.addCleanup(server.disconnect)
        for node, sock in ((server, server_socket), (client, client_socket)):
            generation = node._attach_socket(sock)
            node.receive_thread = threading.Thread(target=node._receive_loop, args=(sock, generation), daemon=True)
            node.receive_thread.start()
        client._offer_mouse_lane(client_socket, client._generation)
        deadline = time.monotonic() + 2
        while (not server._mouse_sender or not server._mouse_sender.ready) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(server._mouse_sender)
        self.assertTrue(server._mouse_sender.ready)
        self.assertTrue(server.send_message({'type': 'mouse_position', 'x': 12, 'y': 34}))
        self.assertTrue(server.send_message({'type': 'mouse_click', 'button': 'left', 'pressed': True}))
        deadline = time.monotonic() + 2
        while not any(m['type'] == 'mouse_click' for m in received) and time.monotonic() < deadline:
            time.sleep(.01)
        click = next(i for i, m in enumerate(received) if m['type'] == 'mouse_click')
        self.assertEqual(received[click - 1]['x'], 12)
        receiver = client._mouse_receiver
        receiver.close()
        deadline = time.monotonic() + 2
        while server._mouse_sender.ready and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertFalse(server._mouse_sender.ready)
        server.send_message({'type': 'mouse_position', 'x': 56, 'y': 78})
        deadline = time.monotonic() + 2
        while not any(m.get('x') == 56 for m in received) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(any(m.get('x') == 56 for m in received))
        client.disconnect(); server.disconnect()
        self.assertFalse(receiver.thread.is_alive())

    def test_old_disconnect_does_not_close_replacement_mouse_lane(self):
        from app.network import NetworkNode
        from unittest.mock import Mock
        node = NetworkNode()
        self.addCleanup(node.disconnect)
        first, second = Mock(), Mock()
        generation = node._attach_socket(first)
        old_lane, new_lane = Mock(), Mock()
        node._mouse_sender = old_lane
        def close_old(conn):
            if conn is first:
                node._attach_socket(second)
                node._mouse_sender = new_lane
        node._close_socket = close_old
        node._disconnect_socket(first, generation)
        old_lane.close.assert_called_once()
        new_lane.close.assert_not_called()

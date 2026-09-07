import unittest
from types import SimpleNamespace
from app.display_topology import Display, MachineDisplayGroup, NativeRect, PlacedMachine, DraftTopology
from app.input_router import InputRouter


class RemoteTopologyTests(unittest.TestCase):
    def test_remote_router_hands_off_between_same_client_displays_only_in_remote_mode(self):
        server = MachineDisplayGroup('server', 'Server', (
            Display('s', NativeRect(0, 0, 100, 100), 100, 0, True),))
        client = MachineDisplayGroup('client', 'Client', (
            Display('c', NativeRect(0, 0, 100, 100), 100, 0, True),
            Display('c2', NativeRect(100, 0, 200, 100), 100, 0, False)))
        topology = DraftTopology('server', (PlacedMachine(server, 0, 0), PlacedMachine(client, 1, 0))).validate().validated.activate(1)
        normal = InputRouter(topology, session_for_machine=lambda _: None, input_effects=SimpleNamespace())
        remote = InputRouter(topology, session_for_machine=lambda _: None, input_effects=SimpleNamespace(), remote_viewport=(100, 100))
        with self.assertRaises(KeyError):
            normal.topology.resolve_edge('client', 'c', 'right', .5)
        edge = remote.topology.resolve_edge('client', 'c', 'right', .5)
        self.assertEqual(edge.mapping.destination_display_id, 'c2')
        self.assertEqual(edge.mapping.destination_machine_id, 'client')
        self.assertEqual(remote.topology.resolve_edge('client', 'c2', 'left', .5).mapping.destination_display_id, 'c')


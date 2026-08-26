import unittest
import threading

import app.session as session_module
from app.server import ConduitServer


class FakeClock:
    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


class Lane:
    def __init__(self, name):
        self.name = name
        self.closed = False

    def close(self):
        self.closed = True


class ScheduledCall:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeScheduler:
    def __init__(self):
        self.calls = []

    def __call__(self, delay, callback):
        call = ScheduledCall(delay, callback)
        self.calls.append(call)
        return call


class ClientSessionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            hasattr(session_module, "SessionRegistry"),
            "Plan 003 requires a bounded SessionRegistry",
        )
        self.clock = FakeClock()
        self.registry = session_module.SessionRegistry(
            "secret",
            clock=self.clock,
            lane_timeout=10.0,
            candidate_timeout=15.0,
        )

    def admit(self, identity, name, address, lane=None):
        return self.registry.authenticate_control(
            "secret",
            peer_identity=identity,
            windows_name=name,
            peer_address=address,
            lane=lane or Lane(f"{identity}-control"),
        )

    def bind(self, admission, purpose, identity, address, lane=None):
        token = getattr(admission, f"{purpose}_token")
        return self.registry.bind_lane(
            token,
            purpose,
            admission.session_id,
            peer_identity=identity,
            peer_address=address,
            lane=lane or Lane(f"{identity}-{purpose}"),
        )

    def make_ready(self, identity, name, address):
        admission = self.admit(identity, name, address)
        self.bind(admission, "data", identity, address)
        self.bind(admission, "file", identity, address)
        return admission

    def test_two_control_sessions_share_password_but_keep_distinct_identities(self):
        first = self.admit("device-a", "ParthPC", "192.0.2.10")
        second = self.admit("device-b", "ParthSurface", "192.0.2.11")

        self.assertEqual(first.outcome, session_module.AdmissionOutcome.ADMITTED)
        self.assertEqual(second.outcome, session_module.AdmissionOutcome.ADMITTED)
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(
            {item.peer_identity for item in self.registry.active_sessions()},
            {"device-a", "device-b"},
        )

    def test_lane_tokens_bind_session_purpose_identity_and_address(self):
        first = self.admit("device-a", "ParthPC", "192.0.2.10")
        second = self.admit("device-b", "ParthSurface", "192.0.2.11")

        rejected = (
            (first.data_token, "file", first.session_id, "device-a", "192.0.2.10"),
            (first.data_token, "data", second.session_id, "device-a", "192.0.2.10"),
            (first.data_token, "data", first.session_id, "device-b", "192.0.2.10"),
            (first.data_token, "data", first.session_id, "device-a", "192.0.2.11"),
        )
        for token, purpose, session_id, identity, address in rejected:
            with self.subTest(purpose=purpose, session_id=session_id, identity=identity, address=address):
                with self.assertRaises(session_module.SessionAuthenticationError):
                    self.registry.bind_lane(
                        token,
                        purpose,
                        session_id,
                        peer_identity=identity,
                        peer_address=address,
                        lane=Lane("wrong"),
                    )

        bound = self.bind(first, "data", "device-a", "192.0.2.10")
        self.assertEqual(bound.peer_identity, "device-a")
        with self.assertRaises(session_module.SessionAuthenticationError):
            self.bind(first, "data", "device-a", "192.0.2.10")

    def test_partial_bundles_expire_independently_and_ready_requires_all_lanes(self):
        first_control = Lane("first-control")
        first = self.admit("device-a", "ParthPC", "192.0.2.10", first_control)
        self.bind(first, "data", "device-a", "192.0.2.10")
        self.assertEqual(self.registry.ready_sessions(), ())

        self.clock.advance(5)
        second = self.make_ready("device-b", "ParthSurface", "192.0.2.11")
        self.assertEqual(
            tuple(item.session_id for item in self.registry.ready_sessions()),
            (second.session_id,),
        )

        self.clock.advance(5)
        expired = self.registry.expire()

        self.assertEqual(tuple(item.session_id for item in expired), (first.session_id,))
        self.assertTrue(first_control.closed)
        self.assertIsNone(self.registry.get(first.session_id))
        self.assertEqual(
            tuple(item.session_id for item in self.registry.ready_sessions()),
            (second.session_id,),
        )

    def test_production_scheduler_releases_partial_bundle_without_another_connection(self):
        scheduler = FakeScheduler()
        registry = session_module.SessionRegistry(
            "secret",
            clock=self.clock,
            lane_timeout=10.0,
            candidate_timeout=15.0,
            scheduler=scheduler,
        )
        lane = Lane("device-a-control")
        admission = registry.authenticate_control(
            "secret",
            peer_identity="device-a",
            windows_name="ParthPC",
            peer_address="192.0.2.10",
            lane=lane,
        )

        self.assertEqual(len(scheduler.calls), 1)
        self.assertEqual(scheduler.calls[0].delay, 10.0)
        self.clock.advance(10.0)
        scheduler.calls[0].callback()

        self.assertIsNone(registry.get(admission.session_id))
        self.assertTrue(lane.closed)

    def test_closing_one_ready_session_leaves_the_other_ready(self):
        first = self.make_ready("device-a", "ParthPC", "192.0.2.10")
        second = self.make_ready("device-b", "ParthSurface", "192.0.2.11")

        self.assertTrue(self.registry.close(first.session_id))

        self.assertIsNone(self.registry.get(first.session_id))
        self.assertEqual(
            tuple(item.session_id for item in self.registry.ready_sessions()),
            (second.session_id,),
        )

    def test_only_one_third_candidate_waits_without_consuming_an_active_slot(self):
        self.make_ready("device-a", "ParthPC", "192.0.2.10")
        self.make_ready("device-b", "ParthSurface", "192.0.2.11")
        pending_lane = Lane("pending-control")

        pending = self.admit("device-c", "TravelPC", "192.0.2.12", pending_lane)
        rejected_lane = Lane("rejected-control")
        rejected = self.admit("device-d", "SparePC", "192.0.2.13", rejected_lane)

        self.assertEqual(pending.outcome, session_module.AdmissionOutcome.PENDING)
        self.assertEqual(rejected.outcome, session_module.AdmissionOutcome.REJECTED)
        self.assertEqual(len(self.registry.active_sessions()), 2)
        self.assertIs(self.registry.pending_candidate(), pending)
        self.assertFalse(pending_lane.closed)
        self.assertTrue(rejected_lane.closed)

    def test_pending_candidate_times_out_at_fifteen_seconds_and_releases_resources(self):
        self.make_ready("device-a", "ParthPC", "192.0.2.10")
        self.make_ready("device-b", "ParthSurface", "192.0.2.11")
        lane = Lane("candidate-control")
        pending = self.admit("device-c", "TravelPC", "192.0.2.12", lane)

        self.clock.advance(14.999)
        self.assertEqual(self.registry.expire(), ())
        self.assertIs(self.registry.pending_candidate(), pending)

        self.clock.advance(0.001)
        outcomes = self.registry.expire()

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].outcome, session_module.AdmissionOutcome.TIMED_OUT)
        self.assertTrue(lane.closed)
        self.assertIsNone(self.registry.pending_candidate())

    def test_reject_is_an_explicit_outcome_and_closes_candidate(self):
        self.make_ready("device-a", "ParthPC", "192.0.2.10")
        self.make_ready("device-b", "ParthSurface", "192.0.2.11")
        lane = Lane("candidate-control")
        self.admit("device-c", "TravelPC", "192.0.2.12", lane)

        decision = self.registry.resolve_candidate(
            session_module.CandidateDecision.REJECT
        )

        self.assertEqual(decision.outcome, session_module.AdmissionOutcome.REJECTED)
        self.assertTrue(lane.closed)
        self.assertIsNone(self.registry.pending_candidate())

    def test_replacement_stays_purple_until_apply_then_inherits_exact_slot_color(self):
        first = self.make_ready("device-a", "ParthPC", "192.0.2.10")
        second = self.make_ready("device-b", "ParthSurface", "192.0.2.11")
        first_session = self.registry.get(first.session_id)
        first_color = first_session.color
        self.admit("device-c", "TravelPC", "192.0.2.12")

        replacement = self.registry.resolve_candidate(
            session_module.CandidateDecision.REPLACE,
            replace_session_id=first.session_id,
        )

        self.assertEqual(replacement.outcome, session_module.AdmissionOutcome.ADMITTED)
        self.assertIsNone(self.registry.get(first.session_id))
        self.assertIsNotNone(self.registry.get(second.session_id))
        promoted = self.registry.get(replacement.session_id)
        self.assertEqual(promoted.slot, first_session.slot)
        self.assertEqual(promoted.color, session_module.PENDING_CLIENT_COLOR)
        self.assertEqual(promoted.replacement_color, first_color)
        self.assertFalse(promoted.ready)
        self.assertIsNone(self.registry.pending_candidate())

        self.assertTrue(self.registry.activate_replacement(replacement.session_id))
        self.assertEqual(promoted.color, first_color)
        self.assertIsNone(promoted.replacement_color)

    def test_replacement_rejects_unknown_target_without_disturbing_sessions(self):
        first = self.make_ready("device-a", "ParthPC", "192.0.2.10")
        second = self.make_ready("device-b", "ParthSurface", "192.0.2.11")
        self.admit("device-c", "TravelPC", "192.0.2.12")

        with self.assertRaises(KeyError):
            self.registry.resolve_candidate(
                session_module.CandidateDecision.REPLACE,
                replace_session_id="missing-session",
            )

        self.assertEqual(
            {item.session_id for item in self.registry.ready_sessions()},
            {first.session_id, second.session_id},
        )
        self.assertIsNotNone(self.registry.pending_candidate())

    def test_duplicate_windows_names_get_conduit_suffixes_without_changing_identity(self):
        first = self.admit("device-a", "ParthPC", "192.0.2.10")
        second = self.admit("device-b", "ParthPC", "192.0.2.11")

        self.assertEqual(self.registry.get(first.session_id).label, "ParthPC")
        self.assertEqual(self.registry.get(second.session_id).label, "ParthPC.2")
        self.assertEqual(self.registry.get(first.session_id).peer_identity, "device-a")
        self.assertEqual(self.registry.get(second.session_id).peer_identity, "device-b")

    def test_candidate_resolution_can_be_consumed_by_the_waiting_control_lane(self):
        self.assertTrue(
            hasattr(self.registry, "take_candidate_resolution"),
            "the waiting control lane needs a bounded resolution handoff",
        )
        self.make_ready("device-a", "ParthPC", "192.0.2.10")
        self.make_ready("device-b", "ParthSurface", "192.0.2.11")
        pending = self.admit("device-c", "TravelPC", "192.0.2.12")

        decision = self.registry.resolve_candidate(
            session_module.CandidateDecision.REJECT
        )

        self.assertIs(
            self.registry.take_candidate_resolution(pending.session_id),
            decision,
        )
        self.assertIsNone(
            self.registry.take_candidate_resolution(pending.session_id)
        )

    def test_registry_shutdown_releases_a_pending_candidate_waiter(self):
        self.make_ready("device-a", "ParthPC", "192.0.2.10")
        self.make_ready("device-b", "ParthSurface", "192.0.2.11")
        lane = Lane("candidate")
        pending = self.admit("device-c", "TravelPC", "192.0.2.12", lane)

        self.registry.close()
        resolution = self.registry.wait_candidate_resolution(
            pending.session_id,
            timeout=0,
        )

        self.assertIsNotNone(resolution)
        self.assertEqual(
            resolution.outcome,
            session_module.AdmissionOutcome.REJECTED,
        )
        self.assertTrue(lane.closed)


class RecordingNetwork:
    def __init__(self):
        self.messages = []
        self.disconnected = []

    def send_message(self, message, session_id=None):
        self.messages.append((session_id, message))
        return True

    def disconnect(self, session_id=None):
        self.disconnected.append(session_id)
        return True


class RecordingFileNetwork(RecordingNetwork):
    def __init__(self):
        super().__init__()
        self.port = 5002
        self.offers = []

    def offer_session(self, token, session_id=None):
        self.offers.append((token, session_id))

    def revoke_session(self, session_id=None):
        self.offers = [offer for offer in self.offers if offer[1] != session_id]


class ConduitServerSessionCompositionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            hasattr(ConduitServer, "_refresh_session_readiness"),
            "ConduitServer must compose readiness by session ID",
        )
        self.registry = session_module.SessionRegistry("secret")
        self.server = ConduitServer.__new__(ConduitServer)
        self.server.session_registry = self.registry
        self.server.control_network = RecordingNetwork()
        self.server.data_network = RecordingNetwork()
        self.server.file_network = RecordingFileNetwork()
        self.server._client_state_lock = threading.RLock()
        self.server._disconnecting_sessions = set()
        self.server._ready_session_ids = set()
        self.server.control_connected = False
        self.server.data_connected = False
        self.server._client_ready = False
        self.ready = []
        self.disconnected = []
        self.server.on_client_connected = self.ready.append
        self.server.on_client_disconnected = self.disconnected.append

    def admit(self, identity, address):
        admission = self.registry.authenticate_control(
            "secret",
            peer_identity=identity,
            windows_name=identity,
            peer_address=address,
            lane=Lane(f"{identity}-control"),
        )
        self.registry.bind_lane(
            admission.data_token,
            "data",
            admission.session_id,
            peer_identity=identity,
            peer_address=address,
            lane=Lane(f"{identity}-data"),
        )
        return admission

    def bind_file(self, admission, identity, address):
        return self.registry.bind_lane(
            admission.file_token,
            "file",
            admission.session_id,
            peer_identity=identity,
            peer_address=address,
            lane=Lane(f"{identity}-file"),
        )

    def test_data_lane_offers_file_lane_to_only_its_control_session(self):
        first = self.admit("device-a", "192.0.2.10")
        second = self.admit("device-b", "192.0.2.11")

        self.server._on_socket_connected("data", {"session_id": first.session_id})
        self.server._on_socket_connected("data", {"session_id": second.session_id})

        self.assertEqual(
            self.server.file_network.offers,
            [(None, first.session_id), (None, second.session_id)],
        )
        self.assertEqual(
            self.server.control_network.messages,
            [
                (
                    first.session_id,
                    {
                        "type": "file_lane_offer",
                        "port": 5002,
                        "session_id": first.session_id,
                    },
                ),
                (
                    second.session_id,
                    {
                        "type": "file_lane_offer",
                        "port": 5002,
                        "session_id": second.session_id,
                    },
                ),
            ],
        )
        self.assertEqual(self.ready, [])

    def test_each_session_becomes_ready_only_after_its_file_lane_binds(self):
        first = self.admit("device-a", "192.0.2.10")
        second = self.admit("device-b", "192.0.2.11")
        self.bind_file(first, "device-a", "192.0.2.10")

        self.server._on_socket_connected("file", {"session_id": first.session_id})
        self.server._on_socket_connected("file", {"session_id": second.session_id})

        self.assertEqual(self.ready, [first.session_id])
        self.assertTrue(self.server._client_ready)

    def test_disconnecting_one_session_leaves_the_other_ready(self):
        first = self.admit("device-a", "192.0.2.10")
        second = self.admit("device-b", "192.0.2.11")
        self.bind_file(first, "device-a", "192.0.2.10")
        self.bind_file(second, "device-b", "192.0.2.11")

        self.server._on_socket_disconnected(
            "control",
            {"session_id": first.session_id},
        )

        self.assertIsNone(self.registry.get(first.session_id))
        self.assertTrue(self.registry.get(second.session_id).ready)
        self.assertEqual(self.disconnected, [first.session_id])
        self.assertTrue(self.server._client_ready)


if __name__ == "__main__":
    unittest.main()

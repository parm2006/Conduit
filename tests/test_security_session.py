import tempfile
import unittest

from app.session import SessionAuthenticationError, SessionRegistry
from app.trust import PeerTrustStore, PendingPeerTrust


class SessionRegistrySecurityTests(unittest.TestCase):
    def test_control_authentication_issues_purpose_bound_one_use_lane_tokens(self):
        coordinator = SessionRegistry("correct horse", clock=lambda: 10.0)

        session = coordinator.authenticate_control(
            "correct horse",
            peer_identity="device-a",
            windows_name="DeviceA",
        )

        self.assertTrue(coordinator.consume_lane(session.data_token, "data", session.session_id))
        with self.assertRaises(SessionAuthenticationError):
            coordinator.consume_lane(session.data_token, "data", session.session_id)
        with self.assertRaises(SessionAuthenticationError):
            coordinator.consume_lane(session.file_token, "data", session.session_id)
        fresh = coordinator.authenticate_control(
            "correct horse",
            peer_identity="device-b",
            windows_name="DeviceB",
        )
        self.assertTrue(coordinator.consume_lane(fresh.file_token, "file", fresh.session_id))

    def test_wrong_password_and_expired_or_cross_session_tokens_are_rejected(self):
        now = [10.0]
        coordinator = SessionRegistry("secret", clock=lambda: now[0], token_ttl=2.0)
        with self.assertLogs("app.session", level="WARNING") as captured:
            with self.assertRaises(SessionAuthenticationError):
                coordinator.authenticate_control(
                    "wrong",
                    peer_identity="device-a",
                    windows_name="DeviceA",
                    peer_address="192.0.2.10",
                )
        self.assertIn("byte_length_match=False", captured.output[0])
        self.assertIn("DeviceA", captured.output[0])
        self.assertNotIn("wrong", captured.output[0])
        self.assertNotIn("secret", captured.output[0])
        session = coordinator.authenticate_control(
            "secret",
            peer_identity="device-a",
            windows_name="DeviceA",
        )
        now[0] = 13.0
        with self.assertRaises(SessionAuthenticationError):
            coordinator.consume_lane(session.data_token, "data", session.session_id)

        fresh = coordinator.authenticate_control(
            "secret",
            peer_identity="device-b",
            windows_name="DeviceB",
        )
        with self.assertRaises(SessionAuthenticationError):
            coordinator.consume_lane(fresh.data_token, "data", "another-session")

    def test_wrong_peer_cannot_consume_the_rightful_peers_token(self):
        coordinator = SessionRegistry("secret")
        session = coordinator.authenticate_control(
            "secret",
            peer_identity="device-a",
            windows_name="DeviceA",
            peer_address="192.0.2.10",
        )

        with self.assertRaises(SessionAuthenticationError):
            coordinator.consume_lane(
                session.data_token,
                "data",
                session.session_id,
                peer_address="192.0.2.11",
            )

        self.assertTrue(
            coordinator.consume_lane(
                session.data_token,
                "data",
                session.session_id,
                peer_address="192.0.2.10",
            )
        )


class PendingPeerTrustTests(unittest.TestCase):
    def test_trust_commits_only_after_approval_authentication_and_lane_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PeerTrustStore(directory)
            peer = store.peer_id("server.local", 5000)
            pending = PendingPeerTrust(store, peer, "ab" * 32)

            pending.approve()
            pending.authenticated()
            self.assertFalse(pending.commit_if_ready())
            self.assertIsNone(store.load(peer))

            pending.lanes_bound()
            self.assertTrue(pending.commit_if_ready())
            self.assertEqual(store.load(peer), "ab" * 32)
            self.assertFalse(pending.commit_if_ready())

    def test_decline_or_failure_never_changes_saved_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PeerTrustStore(directory)
            peer = store.peer_id("server.local", 5000)
            store.commit(peer, "11" * 32)
            pending = PendingPeerTrust(store, peer, "22" * 32)

            pending.decline()
            pending.authenticated()
            pending.lanes_bound()

            self.assertFalse(pending.commit_if_ready())
            self.assertEqual(store.load(peer), "11" * 32)


if __name__ == "__main__":
    unittest.main()

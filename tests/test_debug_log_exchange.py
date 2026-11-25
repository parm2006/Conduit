import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.debug_log_exchange import (
    PeerLogExchange,
    async_exchange_on_stall,
    builds_match,
    compress_log_bytes,
    decompress_log_bytes,
    exchange_endpoint_logs,
    local_build_info,
    read_session_log_bytes,
)


class DebugLogExchangeTests(unittest.TestCase):
    def test_local_build_info_reads_git_dir_and_worktree_gitdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            common = root / "main_git"
            (common / "refs" / "heads" / "codex").mkdir(parents=True)
            (common / "refs" / "heads" / "codex" / "remote").write_text(
                "0123456789abcdef0123456789abcdef01234567\n",
                encoding="utf-8",
            )
            wt_git = common / "worktrees" / "wt1"
            wt_git.mkdir(parents=True)
            (wt_git / "HEAD").write_text("ref: refs/heads/codex/remote\n", encoding="utf-8")
            (wt_git / "commondir").write_text("../..\n", encoding="utf-8")

            wt_repo = root / "wt_repo"
            wt_repo.mkdir()
            (wt_repo / ".git").write_text(f"gitdir: {wt_git}\n", encoding="utf-8")

            info = local_build_info(wt_repo)
            self.assertEqual(info["commit"], "0123456789ab")
            self.assertEqual(info["branch"], "codex/remote")

            missing_repo = root / "no_git"
            missing_repo.mkdir()
            missing = local_build_info(missing_repo)
            self.assertEqual(missing["commit"], "unknown")

    def test_slice_and_compress_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "conduit.log"
            prefix = b"A" * 500
            session = b"SESSION_START\n" + (b"B" * 400) + b"\nSESSION_END\n"
            log_path.write_bytes(prefix + session)

            sliced = read_session_log_bytes(
                log_path,
                start_offset=len(prefix),
                preamble_bytes=64,
                max_bytes=4096,
            )
            self.assertEqual(sliced, prefix[-64:] + session)

            rotated = read_session_log_bytes(
                log_path,
                start_offset=999999,
                preamble_bytes=64,
                max_bytes=4096,
            )
            self.assertEqual(rotated, prefix + session)

            encoded = compress_log_bytes(sliced)
            self.assertEqual(decompress_log_bytes(encoded), sliced)
            self.assertEqual(decompress_log_bytes("not-valid-base64!"), b"")

    def test_two_way_exchange_saves_both_sides_and_checks_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            srv_root = root / "server_appdata"
            cli_root = root / "client_appdata"
            srv_root.mkdir()
            cli_root.mkdir()

            (srv_root / "conduit.log").write_text("SERVER_PRE\n", encoding="utf-8")
            (cli_root / "conduit.log").write_text("CLIENT_PRE\n", encoding="utf-8")

            srv_mgr = PeerLogExchange(
                role="server", machine_id="srv-id", log_root=srv_root, hostname="DESKTOP-SRV"
            )
            cli_mgr = PeerLogExchange(
                role="client", machine_id="cli-id", log_root=cli_root, hostname="LAPTOP-CLI"
            )
            srv_mgr.mark_session_start()
            cli_mgr.mark_session_start()

            with (srv_root / "conduit.log").open("a", encoding="utf-8") as fh:
                fh.write("SERVER_EVENT\n")
            with (cli_root / "conduit.log").open("a", encoding="utf-8") as fh:
                fh.write("CLIENT_EVENT\n")

            def deliver_to_client(msg):
                threading.Thread(
                    target=lambda: cli_mgr.handle_incoming(msg, send_reply=deliver_to_server),
                    daemon=True,
                ).start()
                return True

            def deliver_to_server(msg):
                threading.Thread(
                    target=lambda: srv_mgr.handle_incoming(msg, send_reply=deliver_to_client),
                    daemon=True,
                ).start()
                return True

            saved = srv_mgr.initiate_exchange(
                targets=[("cli-1", deliver_to_client, True)],
                reason="server_stopping",
                timeout=2.0,
            )
            self.assertEqual(len(saved), 1)
            bundle = saved[0]
            self.assertIn("CLIENT_EVENT", (bundle / "peer_conduit.log").read_text(encoding="utf-8"))
            self.assertIn("SERVER_EVENT", (bundle / "local_conduit.log").read_text(encoding="utf-8"))
            meta = json.loads((bundle / "meta.json").read_text(encoding="utf-8"))
            self.assertIn("commit", meta["local"]["build"])
            self.assertIn("commit", meta["peer"]["build"])
            self.assertTrue(meta["build_match"])

            cli_bundles = list((cli_root / "peer_logs").iterdir())
            self.assertEqual(len(cli_bundles), 1)
            self.assertIn("SERVER_EVENT", (cli_bundles[0] / "peer_conduit.log").read_text(encoding="utf-8"))

    def test_builds_match_detects_commit_and_dll_mismatch(self):
        a = {"commit": "abc123456789", "dll_sha256": "111111111111"}
        b = {"commit": "abc123456789", "dll_sha256": "111111111111"}
        c = {"commit": "def987654321", "dll_sha256": "111111111111"}
        d = {"commit": "abc123456789", "dll_sha256": "222222222222"}
        self.assertTrue(builds_match(a, b))
        self.assertFalse(builds_match(a, c))
        self.assertFalse(builds_match(a, d))
        self.assertIsNone(builds_match(a, {"commit": "unknown"}))

    def test_mock_transport_skips_exchange_and_live_stall_respects_cooldown(self):
        mock_net = MagicMock()
        self.assertFalse(PeerLogExchange.is_live_transport(mock_net))

        endpoint = MagicMock()
        endpoint.control_connected = True
        endpoint.control_network = mock_net
        endpoint.peer_log_exchange = PeerLogExchange(role="server", machine_id="s")
        self.assertEqual(exchange_endpoint_logs(endpoint, reason="test"), [])
        self.assertFalse(async_exchange_on_stall(endpoint))

        live_net = MagicMock()
        live_net._debug_log_exchange_allow_wait = True
        endpoint.control_network = live_net
        with patch("app.debug_log_exchange.threading.Thread") as thread_cls:
            self.assertTrue(async_exchange_on_stall(endpoint, cooldown=30.0))
            self.assertFalse(async_exchange_on_stall(endpoint, cooldown=30.0))
            thread_cls.assert_called_once()

    def test_server_and_client_register_log_exchange_and_build_verify(self):
        with patch("app.server.load_identity", return_value=MagicMock()), patch(
            "app.server.NetworkServer"
        ), patch("app.server.FileLaneServer"):
            from app.server import ConduitServer

            server = ConduitServer("pw")
            registered = [c.args[0] for c in server.control_network.register_callback.call_args_list]
            self.assertIn("debug_log_exchange", registered)
            self.assertIn("peer_build_info", registered)
            self.assertEqual(exchange_endpoint_logs(server, reason="test"), [])

        with patch("app.client.PeerTrustStore"), patch(
            "app.client.NetworkClient"
        ), patch("app.client.FileLaneClient"):
            from app.client import ConduitClient

            client = ConduitClient("pw")
            registered = [c.args[0] for c in client.control_network.register_callback.call_args_list]
            self.assertIn("debug_log_exchange", registered)
            self.assertIn("peer_build_info", registered)
            self.assertEqual(exchange_endpoint_logs(client, reason="test"), [])


if __name__ == "__main__":
    unittest.main()

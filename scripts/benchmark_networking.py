"""Repeatable loopback transport benchmark, with no OS input injection.

RTT includes the TLS acknowledgement return path and Python scheduling. This
measures application transport behavior, not input-to-display latency. File
load uses the production TLS file lane and sender ACK window, with an in-memory
receiver (no filesystem throughput claim).
"""
import argparse
from contextlib import ExitStack
import json
import math
from pathlib import Path
import secrets
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace

from app.crypto import IdentityStore
from app.file_transfer.transport import FileLaneClient, FileLaneServer
from app.file_transfer.sender import TransferSender
from app.file_transfer.controller import TransferCancelled
from app.file_transfer.rate_control import AckWindowController
from app.input_dispatcher import InputDispatcher
from app.network import NetworkClient, NetworkServer
from app.session import SessionRegistry
from app.trust import PeerTrustStore


def summarize(observations, offered):
    values = sorted(observations.values())
    return {
        'offered': offered,
        'observed': len(values),
        'coalesced_or_undelivered': offered - len(values),
        'round_trip_ms': {
            'p50': values[math.ceil(len(values) * .5) - 1],
            'p95': values[math.ceil(len(values) * .95) - 1],
            'max': values[-1],
        } if values else None,
    }


def _wait(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError('Benchmark endpoint did not become ready')
        time.sleep(.005)


def _connect(client, port):
    done = threading.Event()
    outcome = []
    client.connect('127.0.0.1', port, lambda ok, error: (outcome.append((ok, error)), done.set()))
    if not done.wait(10) or not outcome[0][0]:
        raise RuntimeError('Benchmark TLS connection failed')


def run_scenario(samples=500, interval_ms=1., transport='udp', bulk=False, fixed_file_window=False, input_kind='absolute'):
    if (isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 100000
            or not math.isfinite(interval_ms) or not 0 <= interval_ms <= 100
            or transport not in ('tls', 'udp') or samples * interval_ms > 60000
            or input_kind not in ('absolute', 'relative', 'keyboard')
            or (input_kind != 'absolute' and transport != 'tls')):
        raise ValueError('Use 1..100000 samples, 0..100ms interval, at most 60 seconds, and tls/udp')
    with ExitStack() as stack:
        directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='conduit-benchmark-')))
        identity = IdentityStore(directory / 'identity', legacy_root=False).load_or_create()
        password = secrets.token_hex(32)
        registry = SessionRegistry(password, lane_timeout=120)
        control = NetworkServer(password, '127.0.0.1', 0, coordinator=registry, identity=identity)
        files = FileLaneServer(host='127.0.0.1', port=0, identity=identity, coordinator=registry)
        for server in (control, files):
            stack.callback(server.stop)
            if not server.start():
                raise RuntimeError('Benchmark loopback listener could not start')
        machine = 'benchmark-' + uuid.uuid4().hex
        client = NetworkClient(password, trust_store=PeerTrustStore(directory / 'trust'),
            fingerprint_approval=lambda fingerprint, peer: fingerprint == identity.fingerprint,
            peer_identity=machine, windows_name='Loopback benchmark',
            enable_mouse_datagrams=transport == 'udp')
        stack.callback(client.disconnect)
        _connect(client, control.port)
        session_id = client.session_info['session_id']
        _wait(lambda: session_id in control.connections)
        lane = control.connections[session_id]
        if transport == 'udp':
            _wait(lambda: lane._mouse_sender is not None and lane._mouse_sender.ready)
        file_client = FileLaneClient(peer_identity=machine)
        stack.callback(file_client.close)
        files.offer_session(client.session_info['file_token'], session_id)
        file_client.connect('127.0.0.1', files.port, identity.fingerprint,
                            client.session_info['file_token'], session_id=session_id)
        _wait(lambda: files.connection(session_id) is not None)
        abort_load = threading.Event()
        def check_load_cancelled(job_id):
            if abort_load.is_set():
                raise TransferCancelled(job_id)
        file_sender = TransferSender(files.connection(session_id),
                                     controller=SimpleNamespace(check_cancelled=check_load_cancelled))
        # Server-side lane callbacks are delivered by the owning multiplexed server.
        files.register_callback("chunk_received", lambda metadata, payload:
            file_sender._on_chunk_received(metadata, payload)
            if metadata.get("session_id") == session_id else None)
        if fixed_file_window:
            file_sender._window_controller = AckWindowController(file_sender.max_in_flight_bytes,
                                                                 file_sender.max_in_flight_bytes)
        condition = threading.Condition()
        sent_at, observations = {}, {}
        delivered_order = []
        file_bytes = 0
        file_ready = threading.Event()
        load_stop = threading.Event()
        errors = []

        def acknowledged(message):
            sample = message.get('sample')
            now = time.perf_counter()
            with condition:
                if sample in sent_at and sample not in observations:
                    observations[sample] = (now - sent_at[sample]) * 1000
                    condition.notify_all()

        def acknowledge_sample(sample):
            delivered_order.append(sample)
            client.send_message({'type': 'benchmark_ack', 'sample': sample})

        def receive_position(message):
            acknowledge_sample(message['x'])

        def receive_relative(message):
            for dx, dy in message['deltas']:
                acknowledge_sample(dx - 1)


        def receive_chunk(metadata, payload):
            nonlocal file_bytes
            with condition:
                file_bytes += len(payload)
            file_client.send({'type': 'chunk_received', 'job_id': metadata['job_id'],
                              'relative_path': metadata['relative_path'], 'offset': metadata['offset']})
            file_ready.set()

        control.register_callback('benchmark_ack', acknowledged)
        client.register_callback('mouse_position', receive_position)
        client.register_callback('mouse_move_batch', receive_relative)
        for kind in ('key_press', 'key_release'):
            client.register_callback(kind, lambda message: acknowledge_sample(message['benchmark_sample']))
        file_client.register_callback('chunk', receive_chunk)
        send_calls = 0
        send_events = 0
        udp_sends = 0
        if transport == 'udp':
            original_udp_send = lane._mouse_sender.send_position
            def count_udp_send(x, y):
                nonlocal udp_sends
                sent = original_udp_send(x, y)
                udp_sends += int(sent)
                return sent
            lane._mouse_sender.send_position = count_udp_send

        def send_input(message):
            nonlocal send_calls, send_events
            sent = lane.send_message(message)
            send_calls += 1
            send_events += len(message['deltas']) if message['type'] == 'mouse_move_batch' else 1
            return sent

        counted_lane = SimpleNamespace(send_message=send_input)
        dispatcher = InputDispatcher(lane_for_session=lambda key: counted_lane,
                                     on_failure=lambda key, reason: errors.append(reason))
        stack.callback(dispatcher.stop_all)
        dispatcher.start_session(session_id)
        load_started = time.perf_counter()

        def generate_load():
            chunk = secrets.token_bytes(256 * 1024)
            offset = 0
            job_id = uuid.uuid4().hex
            try:
                deadline = time.monotonic() + 65
                while not load_stop.is_set() and time.monotonic() < deadline:
                    offset = file_sender._send_chunk(job_id, 'benchmark.bin', offset, chunk, False)
                file_sender._wait_for_chunk_acks(job_id, 'benchmark.bin')
            except Exception as error:
                errors.append(type(error).__name__)

        load = None
        if bulk:
            load = threading.Thread(target=generate_load, daemon=True, name='benchmark-file-load')
            load.start()
        try:
            if bulk and not file_ready.wait(5):
                raise RuntimeError('Benchmark file load did not start')
            started = time.perf_counter()
            lateness = {}
            for sample in range(samples):
                due = started + sample * interval_ms / 1000
                delay = due - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                with condition:
                    sent_at[sample] = time.perf_counter()
                    lateness[sample] = max(0., sent_at[sample] - due) * 1000
                if input_kind == 'absolute':
                    accepted = dispatcher.enqueue_position(session_id, sample, 0)
                elif input_kind == 'relative':
                    accepted = dispatcher.enqueue_move(session_id, sample + 1, 0)
                else:
                    accepted = dispatcher.enqueue_discrete(session_id, {
                        'type': 'key_press' if sample % 2 == 0 else 'key_release',
                        'key': {'type': 'char', 'value': 'a'}, 'benchmark_sample': sample})
                if not accepted:
                    raise RuntimeError('Benchmark input queue rejected a position')
            offering_duration = time.perf_counter() - started
            with condition:
                final = condition.wait_for(lambda: samples - 1 in observations, timeout=3)
            # Wait for the dispatcher to finish its timing record, not just the peer ACK.
            def input_finished():
                stats = dispatcher.latency_snapshot(session_id)
                return (stats['completed'] == send_calls
                        and send_events + stats['coalesced_positions'] == samples)
            _wait(input_finished)
        finally:
            load_stop.set()
            if load is not None:
                load.join(3)
                if load.is_alive():
                    abort_load.set()
                    with file_sender._chunk_ack_condition:
                        file_sender._chunk_ack_condition.notify_all()
                    files.close()
                    load.join(1)
                    errors.append('file load exceeded shutdown deadline')
                    if load.is_alive():
                        raise RuntimeError('Benchmark worker failed to stop after cancellation')
        duration = time.perf_counter() - load_started
        with condition:
            result = summarize(observations, samples)
            acknowledged_bytes = file_bytes
        result.update({
            'transport': transport, 'bulk': bulk, 'input_kind': input_kind,
            'actual_input_sends': {'udp': udp_sends, 'tls': send_calls - udp_sends},
            'delivery_order_valid': delivered_order == sorted(set(delivered_order)),
            'file_window': 'fixed' if fixed_file_window else 'adaptive',
            'interval_ms': interval_ms,
            'offering_duration_ms': offering_duration * 1000,
            'producer_lateness_ms': summarize(lateness, samples)['round_trip_ms'],
            'final_position_observed': final,
            'queue': dispatcher.latency_snapshot(session_id),
            'file_bytes_acknowledged': acknowledged_bytes,
            'file_mib_per_second': acknowledged_bytes / (1024 * 1024) / duration,
            'errors': errors,
        })
        if input_kind != 'absolute' and (len(observations) != samples or not result['delivery_order_valid']):
            errors.append('Reliable input lost or reordered samples')
        if not final or errors:
            raise RuntimeError('Benchmark incomplete: ' + json.dumps(result))
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=500)
    parser.add_argument('--interval-ms', type=float, default=1.)
    parser.add_argument('--fixed-file-window', action='store_true', help='Compare with the previous fixed 1 MiB file window')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--input-kind', choices=('all', 'absolute', 'relative', 'keyboard'), default='all')
    args = parser.parse_args(argv)
    report = {
        'scope': 'loopback transport RTT; not physical input-to-display latency',
        'ack_return_path': 'TLS',
        'file_receiver': 'memory; no filesystem writes',
        'scenarios': [run_scenario(args.samples, args.interval_ms, transport, bulk, args.fixed_file_window, kind)
                      for kind in ('absolute', 'relative', 'keyboard')
                      if args.input_kind in ('all', kind)
                      for transport in (('tls', 'udp') if kind == 'absolute' else ('tls',))
                      for bulk in (False, True)],
    }
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)


if __name__ == '__main__':
    main()

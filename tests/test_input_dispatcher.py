import threading
import unittest

from app.input_dispatcher import InputDispatcher


class RecordingLane:
    def __init__(self):
        self.messages = []
        self.condition = threading.Condition()
        self.send_result = True

    def send_message(self, message):
        with self.condition:
            self.messages.append(dict(message))
            self.condition.notify_all()
        return self.send_result

    def wait_for_count(self, count, timeout=1):
        with self.condition:
            return self.condition.wait_for(
                lambda: len(self.messages) >= count,
                timeout,
            )


class BlockingLane(RecordingLane):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def send_message(self, message):
        with self.condition:
            self.messages.append(dict(message))
            self.condition.notify_all()
        self.entered.set()
        self.release.wait(2)
        self.finished.set()
        return self.send_result


class GateFirstLane(RecordingLane):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self._first = True

    def send_message(self, message):
        if self._first:
            self._first = False
            self.entered.set()
            self.release.wait(2)
        return super().send_message(message)


class InputDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.lanes = {
            "session-1": RecordingLane(),
            "session-2": RecordingLane(),
        }
        self.failures = []
        self.failure_seen = threading.Event()
        self.dispatcher = InputDispatcher(
            lane_for_session=self.lanes.get,
            on_failure=self._record_failure,
        )
        self.addCleanup(self.dispatcher.stop_all)

    def _record_failure(self, session_id, reason):
        self.failures.append((session_id, reason))
        self.failure_seen.set()

    def test_blocked_session_does_not_block_enqueue_or_other_session(self):
        blocked = BlockingLane()
        self.lanes["session-1"] = blocked
        self.assertTrue(self.dispatcher.start_session("session-1"))
        self.assertTrue(self.dispatcher.start_session("session-2"))
        self.assertTrue(self.dispatcher.enqueue_discrete(
            "session-1",
            {"type": "key_press", "key": {"value": "a"}},
        ))
        self.assertTrue(blocked.entered.wait(0.2))

        enqueue_returned = threading.Event()
        producer = threading.Thread(target=lambda: (
            self.dispatcher.enqueue_move("session-1", 3, 4),
            enqueue_returned.set(),
        ))
        producer.start()
        self.assertTrue(enqueue_returned.wait(0.2))
        producer.join(1)
        self.assertTrue(self.dispatcher.enqueue_discrete(
            "session-2",
            {"type": "key_press", "key": {"value": "b"}},
        ))
        self.assertTrue(self.lanes["session-2"].wait_for_count(1, 0.2))

        blocked.release.set()
        self.assertTrue(blocked.finished.wait(0.2))

    def test_movement_batches_preserve_original_deltas_and_discrete_order(self):
        lane = GateFirstLane()
        self.lanes["session-1"] = lane
        self.assertTrue(self.dispatcher.start_session("session-1"))
        gate = {"type": "key_press", "key": {"value": "g"}}
        click = {"type": "mouse_click", "button": "left", "pressed": True}
        self.assertTrue(self.dispatcher.enqueue_discrete("session-1", gate))
        self.assertTrue(lane.entered.wait(0.2))
        first_deltas = [(index, -index) for index in range(40)]
        last_deltas = [(100 + index, index) for index in range(3)]
        for dx, dy in first_deltas:
            self.assertTrue(self.dispatcher.enqueue_move("session-1", dx, dy))
        self.assertTrue(self.dispatcher.enqueue_discrete("session-1", click))
        for dx, dy in last_deltas:
            self.assertTrue(self.dispatcher.enqueue_move("session-1", dx, dy))
        lane.release.set()
        self.assertTrue(lane.wait_for_count(5, 1))

        self.assertEqual(lane.messages[0], gate)
        click_index = lane.messages.index(click)
        before_click = lane.messages[1:click_index]
        after_click = lane.messages[click_index + 1:]
        self.assertTrue(all(
            message["type"] == "mouse_move_batch"
            and len(message["deltas"]) <= 32
            for message in before_click + after_click
        ))
        self.assertEqual(
            [tuple(delta) for message in before_click for delta in message["deltas"]],
            first_deltas,
        )
        self.assertEqual(
            [tuple(delta) for message in after_click for delta in message["deltas"]],
            last_deltas,
        )

    def test_movement_overflow_fails_once_at_512_pending_deltas(self):
        lane = BlockingLane()
        self.lanes["session-1"] = lane
        self.assertTrue(self.dispatcher.start_session("session-1"))
        self.assertTrue(self.dispatcher.enqueue_discrete(
            "session-1",
            {"type": "key_press", "key": {"value": "gate"}},
        ))
        self.assertTrue(lane.entered.wait(0.2))
        for index in range(512):
            self.assertTrue(self.dispatcher.enqueue_move("session-1", index, 0))

        self.assertFalse(self.dispatcher.enqueue_move("session-1", 513, 0))
        self.assertTrue(self.failure_seen.wait(0.2))
        self.assertFalse(self.dispatcher.enqueue_move("session-1", 514, 0))
        self.assertEqual(len(self.failures), 1)
        lane.release.set()

    def test_discrete_overflow_fails_once_at_256_pending_records(self):
        lane = BlockingLane()
        self.lanes["session-1"] = lane
        self.assertTrue(self.dispatcher.start_session("session-1"))
        self.assertTrue(self.dispatcher.enqueue_move("session-1", 1, 1))
        self.assertTrue(lane.entered.wait(0.2))
        for index in range(256):
            self.assertTrue(self.dispatcher.enqueue_discrete(
                "session-1",
                {"type": "key_press", "key": {"value": str(index)}},
            ))

        self.assertFalse(self.dispatcher.enqueue_discrete(
            "session-1",
            {"type": "key_press", "key": {"value": "overflow"}},
        ))
        self.assertTrue(self.failure_seen.wait(0.2))
        self.assertFalse(self.dispatcher.enqueue_discrete(
            "session-1",
            {"type": "key_release", "key": {"value": "later"}},
        ))
        self.assertEqual(len(self.failures), 1)
        lane.release.set()

    def test_send_failure_callback_runs_outside_queue_lock(self):
        lane = self.lanes["session-1"]
        lane.send_result = False
        callback_completed = threading.Event()
        dispatcher = None

        def failure(session_id, reason):
            dispatcher.stop_session(session_id)
            callback_completed.set()

        dispatcher = InputDispatcher(
            lane_for_session=self.lanes.get,
            on_failure=failure,
        )
        self.addCleanup(dispatcher.stop_all)
        self.assertTrue(dispatcher.start_session("session-1"))
        self.assertTrue(dispatcher.enqueue_discrete(
            "session-1",
            {"type": "key_press", "key": {"value": "x"}},
        ))

        self.assertTrue(callback_completed.wait(0.2))
        self.assertFalse(dispatcher.enqueue_move("session-1", 1, 2))

    def test_stop_rejects_new_work_without_joining_blocked_worker(self):
        lane = BlockingLane()
        self.lanes["session-1"] = lane
        self.assertTrue(self.dispatcher.start_session("session-1"))
        self.assertTrue(self.dispatcher.enqueue_discrete(
            "session-1",
            {"type": "key_press", "key": {"value": "x"}},
        ))
        self.assertTrue(lane.entered.wait(0.2))

        stopped = threading.Event()
        stopper = threading.Thread(target=lambda: (
            self.dispatcher.stop_session("session-1"),
            stopped.set(),
        ))
        stopper.start()
        self.assertTrue(stopped.wait(0.2))
        stopper.join(1)
        self.assertFalse(self.dispatcher.enqueue_discrete(
            "session-1",
            {"type": "key_release", "key": {"value": "x"}},
        ))
        lane.release.set()


if __name__ == "__main__":
    unittest.main()

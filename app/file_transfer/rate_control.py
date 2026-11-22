class AckWindowController:
    """Bound queued file bytes using receiver ACK delay, including disk pressure.

    The sender owns synchronization. This complements TCP congestion control;
    ACK delay is deliberately not described as a measurement of network RTT.
    """

    def __init__(self, maximum, minimum=256 * 1024):
        if maximum <= 0 or minimum <= 0:
            raise ValueError("window bounds must be positive")
        self.maximum = int(maximum)
        self.minimum = min(int(minimum), self.maximum)
        self.window = min(self.maximum, 2 * self.minimum)
        self._baseline = None
        self._clean_bytes = 0
        self._next_adjustment = 0.0

    def observe_ack(self, size, delay, now):
        delay = max(0.0, delay)
        self._baseline = delay if self._baseline is None else min(self._baseline, delay)
        if delay - self._baseline >= 0.015:
            self._clean_bytes = 0
            if now >= self._next_adjustment:
                self.window = max(self.minimum, self.window // 2)
                # ACKs for the same outstanding burst should not each halve it.
                self._next_adjustment = now + max(delay, 0.015)
        elif now >= self._next_adjustment:
            self._clean_bytes += size
            if self._clean_bytes >= self.window:
                self._clean_bytes = 0
                self.window = min(self.maximum, self.window + self.minimum)
        return self.window


class BalancedRateController:
    """Computes a file-lane budget; control traffic never consumes this budget."""

    def __init__(self, spare_bytes_per_second, baseline_rtt_ms):
        if spare_bytes_per_second <= 0 or baseline_rtt_ms < 0:
            raise ValueError("rate and baseline RTT must be positive")
        self._balanced_rate = int(spare_bytes_per_second * 0.5)
        self._baseline_rtt_ms = baseline_rtt_ms
        self.allowed_bytes_per_second = self._balanced_rate
        self._control_stalls = 0

    def observe_rtt(self, rtt_ms):
        delta = rtt_ms - self._baseline_rtt_ms
        if delta >= 100:
            target = 0
        elif delta >= 40:
            target = int(self._balanced_rate * 0.2)
        elif delta >= 15:
            target = int(self._balanced_rate * 0.6)
        else:
            self._control_stalls = 0
            target = min(
                self._balanced_rate,
                self.allowed_bytes_per_second + max(1, int(self._balanced_rate * 0.1)),
            )
        self.allowed_bytes_per_second = target
        return target

    def note_control_stall(self):
        self._control_stalls += 1
        if self._control_stalls >= 2:
            self.allowed_bytes_per_second = 0
        else:
            self.allowed_bytes_per_second = min(
                self.allowed_bytes_per_second,
                int(self._balanced_rate * 0.2),
            )
        return self.allowed_bytes_per_second

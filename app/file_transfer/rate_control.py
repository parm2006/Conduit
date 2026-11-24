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

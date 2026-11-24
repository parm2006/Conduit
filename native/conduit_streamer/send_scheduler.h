// send_scheduler.h - pure sender pacing logic (no GPU, no sleeping, fully testable).
//
// Replaces "encode the retained texture every 1/fps regardless of change" with:
//  * Encode only when the desktop actually changed (dirty), at most fps times/sec.
//  * Trailing edge guaranteed: if a change arrives inside the min interval, the
//    LATEST content is still encoded when the interval elapses (nothing is lost).
//  * Idle keepalive: when nothing changes, re-encode the retained texture at a low
//    rate (default 4 Hz). This makes "idle desktop" distinguishable from "dead
//    stream" using the existing receiver frame-counter liveness, with no protocol
//    change. Keepalive frames are tiny P-frames (verify with the trace output).
//  * A keyframe request marks dirty so an IDR is produced even on an idle desktop.
#pragma once
#include <cstdint>

namespace conduit {

class SendScheduler {
public:
    enum class Action { Wait, Encode, Keepalive };

    explicit SendScheduler(uint32_t fps, uint32_t keepalive_ms = 250)
        : min_interval_us_(1000000ull / (fps ? fps : 1u)),
          keepalive_us_(static_cast<uint64_t>(keepalive_ms) * 1000ull) {}

    void OnCapture(bool has_new_frame) { if (has_new_frame) dirty_ = true; }
    void MarkDirty() { dirty_ = true; }

    Action Poll(uint64_t now_us) {
        if (!started_) { started_ = true; last_tx_us_ = now_us; }
        if (dirty_) {
            return (!encoded_once_ || now_us - last_encode_us_ >= min_interval_us_) ? Action::Encode
                                                                                    : Action::Wait;
        }
        if (encoded_once_ && now_us - last_tx_us_ >= keepalive_us_) return Action::Keepalive;
        return Action::Wait;
    }

    void OnEncoded(uint64_t now_us) {
        dirty_ = false;
        encoded_once_ = true;
        last_encode_us_ = now_us;
        last_tx_us_ = now_us;
    }

    // Timeout to hand to the capture call so we wake exactly when work is due.
    uint32_t WaitMs(uint64_t now_us) const {
        uint64_t due;
        if (dirty_) {
            if (!encoded_once_) return 0;
            due = last_encode_us_ + min_interval_us_;
        } else if (encoded_once_) {
            due = last_tx_us_ + keepalive_us_;
        } else {
            return 50;  // waiting for the very first desktop frame
        }
        if (due <= now_us) return 0;
        const uint64_t ms = (due - now_us + 999) / 1000;
        return static_cast<uint32_t>(ms > 1000 ? 1000 : ms);
    }

private:
    uint64_t min_interval_us_, keepalive_us_;
    bool dirty_ = false, encoded_once_ = false, started_ = false;
    uint64_t last_encode_us_ = 0, last_tx_us_ = 0;
};

}  // namespace conduit

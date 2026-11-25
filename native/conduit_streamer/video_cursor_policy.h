#pragma once
#include "protocol.h"

namespace conduit {
// -1: legacy/unknown; 0: viewer supplies pointer; 1: pointer is in image.
inline int CursorPolicyFromFlags(uint8_t flags) {
    if (!(flags & STREAMER_FLAG_CURSOR_KNOWN)) return -1;
    return (flags & STREAMER_FLAG_CURSOR_IN_FRAME) ? 1 : 0;
}
inline uint8_t CursorPolicyFlags(int policy) {
    return policy < 0 ? 0 : STREAMER_FLAG_CURSOR_KNOWN |
        (policy ? STREAMER_FLAG_CURSOR_IN_FRAME : 0);
}
struct CursorObservation {
    bool known = false;
    bool system_visible = false;
    bool overlay_visible = false;
};
inline bool CursorObservationsAgree(const CursorObservation& before, const CursorObservation& after) {
    return before.known && after.known && before.system_visible == after.system_visible &&
        before.overlay_visible == after.overlay_visible;
}
class CaptureCursorPolicy {
public:
    void Reset() { value_ = -1; separate_visible_ = -1; }
    int Value() const { return value_; }
    void ObservePointer(bool mouse_updated, bool separate_visible) {
        if (mouse_updated) separate_visible_ = separate_visible ? 1 : 0;
    }
    bool Update(bool mouse_updated, bool separate_visible) {
        // Legacy callers assume a showing system cursor. Capture uses observations.
        const CursorObservation showing{true, true, false};
        return Update(mouse_updated, separate_visible, showing, showing);
    }
    bool Update(bool mouse_updated, bool separate_visible,
                const CursorObservation& before, const CursorObservation& after) {
        ObservePointer(mouse_updated, separate_visible);
        if (!CursorObservationsAgree(before, after)) return false;
        int next;
        if (!after.system_visible) next = after.overlay_visible ? 1 : 0;
        else {
            if (separate_visible_ < 0) return false;
            next = separate_visible_ ? 0 : 1;
        }
        const bool changed = next != value_;
        value_ = next;
        return changed;
    }
private:
    int value_ = -1;
    int separate_visible_ = -1;
};
} // namespace conduit

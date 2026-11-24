#pragma once
#include "protocol.h"

namespace conduit {
// -1: legacy/unknown; 0: separate pointer; 1: image-owned or source-hidden.
inline int CursorPolicyFromFlags(uint8_t flags) {
    if (!(flags & STREAMER_FLAG_CURSOR_KNOWN)) return -1;
    return (flags & STREAMER_FLAG_CURSOR_IN_FRAME) ? 1 : 0;
}
inline uint8_t CursorPolicyFlags(int policy) {
    return policy < 0 ? 0 : STREAMER_FLAG_CURSOR_KNOWN |
        (policy ? STREAMER_FLAG_CURSOR_IN_FRAME : 0);
}
class CaptureCursorPolicy {
public:
    void Reset() { value_ = -1; }
    int Value() const { return value_; }
    bool Update(bool mouse_updated, bool separate_visible) {
        if (!mouse_updated) return false;
        const int next = separate_visible ? 0 : 1;
        const bool changed = next != value_;
        value_ = next;
        return changed;
    }
private:
    int value_ = -1;
};
} // namespace conduit

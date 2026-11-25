#include "video_cursor_policy.h"
#include <cstdio>

#define CHECK(condition) do { if (!(condition)) { \
    std::fprintf(stderr, "line %d: %s\n", __LINE__, #condition); return 1; } } while (0)

int main() {
    using conduit::CursorObservation;
    const CursorObservation showing{true, true, false};
    const CursorObservation hidden{true, false, false};
    const CursorObservation fallback{true, false, true};
    const CursorObservation unknown{};
    CHECK(conduit::CursorObservationsAgree(showing, showing));
    CHECK(!conduit::CursorObservationsAgree(unknown, showing));
    CHECK(!conduit::CursorObservationsAgree(hidden, showing));
    CHECK(!conduit::CursorObservationsAgree(hidden, fallback));
    conduit::CaptureCursorPolicy policy;
    CHECK(policy.Value() == -1);
    CHECK(policy.Update(true, true, showing, showing));
    CHECK(policy.Value() == 0); // Separate hardware pointer: viewer supplies it.
    CHECK(policy.Update(true, false, showing, showing));
    CHECK(policy.Value() == 1); // System pointer is already part of the image.
    CHECK(policy.Update(true, false, hidden, hidden));
    CHECK(policy.Value() == 0); // Typing/hot-unplug hidden cursor is not image-owned.
    CHECK(policy.Update(false, false, fallback, fallback));
    CHECK(policy.Value() == 1); // Own software arrow is in the captured pixels.
    CHECK(!policy.Update(false, false, unknown, unknown));
    CHECK(policy.Value() == 1);
    CHECK(!policy.Update(false, false, fallback, hidden));
    CHECK(policy.Value() == 1); // A visibility race must not reinterpret this image.
    CHECK(policy.Update(false, false, hidden, hidden));
    CHECK(policy.Value() == 0); // Overlay removal can change policy without DXGI mouse update.
    CHECK(policy.Update(false, false, showing, showing));
    CHECK(policy.Value() == 1); // Retained separate=false survives the hidden interval.
    CHECK(policy.Update(true, true, showing, showing));
    CHECK(policy.Value() == 0); // Replugged separate system pointer needs no fallback image.
    CHECK(!policy.Update(false, false, showing, showing));
    CHECK(policy.Value() == 0); // Pointer fields are meaningless when timestamp is zero.
    policy.Reset();
    CHECK(!policy.Update(false, false, showing, showing));
    CHECK(policy.Value() == -1); // Unknown DXGI ownership is never guessed.
    CHECK(policy.Update(false, false, hidden, hidden));
    CHECK(policy.Value() == 0); // Known-hidden source still needs a usable viewer arrow.
    CHECK(policy.Update(true, false, showing, showing));
    CHECK(policy.Value() == 1);
    policy.ObservePointer(true, true); // A dropped race still carries useful DXGI state.
    CHECK(policy.Value() == 1); // Cached image policy stays unchanged.
    CHECK(policy.Update(false, false, showing, showing));
    CHECK(policy.Value() == 0); // Next stable image uses the retained pointer observation.
    std::puts("source-hidden, software fallback, hotplug, and observation races passed");
    return 0;
}

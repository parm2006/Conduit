#include "packet_assembler.h"
#include "video_cursor_policy.h"
#include <cstdio>
int main() {
    PacketAssembler assembler;
    int called = 0;
    assembler.Initialize([&](AssembledFrame&& frame) {
        assembler.Reset(); // callback must run outside assembler lock
        if (frame.data.empty()) return;
        ++called;
    }, [] {});
    PacketHeader header = {};
    header.frame_id = 1; header.total_fragments = 1;
    const uint8_t data = 7;
    assembler.IngestFragment(header, &data, 1);
    if (called != 1) return 1;
    header.frame_id = 2; header.total_fragments = 2; header.fragment_index = 0;
    assembler.IngestFragment(header, &data, 1);
    header.total_fragments = 3; header.fragment_index = 1;
    assembler.IngestFragment(header, &data, 1);
    if (called != 1) return 2; // inconsistent authenticated fragments must not complete
    assembler.Reset();
    header.frame_id = 3; header.total_fragments = 65535; header.fragment_index = 0;
    assembler.IngestFragment(header, &data, 1); // impossible frame count: reject before allocation
    header.total_fragments = 1;
    assembler.IngestFragment(header, &data, 1);
    if (called != 2) return 3;
    conduit::CaptureCursorPolicy policy;
    if (policy.Value() != -1) return 4;
    if (!policy.Update(true, false) || policy.Value() != 1) return 5;
    if (policy.Update(false, true) || policy.Value() != 1) return 6;
    if (!policy.Update(true, true) || policy.Value() != 0) return 7;
    policy.Reset();
    if (policy.Value() != -1) return 8;
    for (int mode : {-1, 0, 1}) {
        if (conduit::CursorPolicyFromFlags(conduit::CursorPolicyFlags(mode)) != mode) return 9;
    }
    PacketAssembler cursor_assembler;
    int delivered_mode = -2, delivered_count = 0;
    cursor_assembler.Initialize([&](AssembledFrame&& frame) {
        delivered_mode = frame.cursor_in_frame; ++delivered_count;
    }, [] {});
    header = {};
    header.frame_id = 1; header.total_fragments = 2;
    header.flags = conduit::CursorPolicyFlags(1);
    cursor_assembler.IngestFragment(header, &data, 1);
    header.fragment_index = 1;
    cursor_assembler.IngestFragment(header, &data, 1);
    if (delivered_count != 1 || delivered_mode != 1) return 10;
    header.frame_id = 2; header.fragment_index = 0;
    cursor_assembler.IngestFragment(header, &data, 1);
    header.fragment_index = 1; header.flags = conduit::CursorPolicyFlags(0);
    cursor_assembler.IngestFragment(header, &data, 1);
    if (delivered_count != 1) return 11; // conflicting policies must not complete
    header.frame_id = 3; header.total_fragments = 1; header.fragment_index = 0;
    cursor_assembler.IngestFragment(header, &data, 1);
    if (delivered_count != 2 || delivered_mode != 0) return 12;
    header.frame_id = 4; header.flags = 0; // old sender stays explicitly unknown
    cursor_assembler.IngestFragment(header, &data, 1);
    if (delivered_count != 3 || delivered_mode != -1) return 13;
    std::puts("assembler callback, metadata consistency, and bounds passed");
    return 0;
}

#include "packet_assembler.h"
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
    std::puts("assembler callback, metadata consistency, and bounds passed");
    return 0;
}

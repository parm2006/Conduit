#ifndef CONDUIT_STREAMER_PACKET_ASSEMBLER_H
#define CONDUIT_STREAMER_PACKET_ASSEMBLER_H

#include "protocol.h"
#include <map>
#include <vector>
#include <functional>
#include <mutex>
#include <stdint.h>

struct AssembledFrame {
    std::vector<uint8_t> data;
    uint32_t frame_id;
    uint32_t timestamp_ms;
    bool is_keyframe;
};

typedef std::function<void(AssembledFrame&&)> FrameReadyCallback;
typedef std::function<void()> KeyframeNeededCallback;

class PacketAssembler {
public:
    PacketAssembler();
    PacketAssembler(FrameReadyCallback on_frame, KeyframeNeededCallback on_keyframe_needed);
    ~PacketAssembler();

    void Initialize(FrameReadyCallback on_frame, KeyframeNeededCallback on_keyframe_needed);
    void IngestFragment(const PacketHeader& header, const uint8_t* payload, size_t payload_len);
    void Reset();

private:
    struct PendingFrame {
        uint32_t frame_id;
        uint32_t timestamp_ms;
        uint16_t total_fragments;
        uint16_t received_fragments;
        bool is_keyframe;
        std::map<uint16_t, std::vector<uint8_t>> fragments;
    };

    FrameReadyCallback m_on_frame;
    KeyframeNeededCallback m_on_keyframe_needed;
    std::mutex m_mutex;

    uint32_t m_latest_completed_frame;
    std::map<uint32_t, PendingFrame> m_pending_frames;
};

#endif // CONDUIT_STREAMER_PACKET_ASSEMBLER_H

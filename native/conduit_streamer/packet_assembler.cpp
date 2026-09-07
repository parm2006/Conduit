#include "packet_assembler.h"

PacketAssembler::PacketAssembler()
    : m_latest_completed_frame(0)
{
}

PacketAssembler::PacketAssembler(FrameReadyCallback on_frame, KeyframeNeededCallback on_keyframe_needed)
    : m_on_frame(std::move(on_frame))
    , m_on_keyframe_needed(std::move(on_keyframe_needed))
    , m_latest_completed_frame(0)
{
}

void PacketAssembler::Initialize(FrameReadyCallback on_frame, KeyframeNeededCallback on_keyframe_needed) {
    Reset();
    std::lock_guard<std::mutex> lock(m_mutex);
    m_on_frame = std::move(on_frame);
    m_on_keyframe_needed = std::move(on_keyframe_needed);
}

PacketAssembler::~PacketAssembler() {
    Reset();
}

void PacketAssembler::Reset() {
    std::lock_guard<std::mutex> lock(m_mutex);
    m_pending_frames.clear();
    m_latest_completed_frame = 0;
}

void PacketAssembler::IngestFragment(const PacketHeader& header, const uint8_t* payload, size_t payload_len) {
    if (!payload || payload_len == 0 || header.total_fragments == 0 || header.fragment_index >= header.total_fragments) {
        return;
    }

    std::lock_guard<std::mutex> lock(m_mutex);

    // Drop stale frames older than latest completed
    if (m_latest_completed_frame > 0 && header.frame_id < m_latest_completed_frame) {
        return;
    }

    // Prune very old incomplete pending frames (keep at most 32 frames in buffer)
    while (m_pending_frames.size() > 32) {
        auto oldest = m_pending_frames.begin();
        if (oldest->second.is_keyframe && m_on_keyframe_needed) {
            m_on_keyframe_needed();
        }
        m_pending_frames.erase(oldest);
    }

    auto& pending = m_pending_frames[header.frame_id];
    if (pending.fragments.empty()) {
        pending.frame_id = header.frame_id;
        pending.timestamp_ms = header.timestamp_ms;
        pending.total_fragments = header.total_fragments;
        pending.received_fragments = 0;
        pending.is_keyframe = (header.flags & STREAMER_FLAG_KEYFRAME) != 0;
    }

    if (pending.fragments.find(header.fragment_index) == pending.fragments.end()) {
        pending.fragments[header.fragment_index].assign(payload, payload + payload_len);
        pending.received_fragments++;
    }

    // Check if frame is complete
    if (pending.received_fragments == pending.total_fragments) {
        size_t total_size = 0;
        for (const auto& kv : pending.fragments) {
            total_size += kv.second.size();
        }

        AssembledFrame frame;
        frame.data.reserve(total_size);
        for (auto& kv : pending.fragments) {
            frame.data.insert(frame.data.end(), kv.second.begin(), kv.second.end());
        }
        frame.frame_id = pending.frame_id;
        frame.timestamp_ms = pending.timestamp_ms;
        frame.is_keyframe = pending.is_keyframe;

        m_latest_completed_frame = frame.frame_id;

        // Clean up completed and preceding frames
        auto it = m_pending_frames.begin();
        while (it != m_pending_frames.end() && it->first <= frame.frame_id) {
            it = m_pending_frames.erase(it);
        }

        if (m_on_frame) {
            m_on_frame(std::move(frame));
        }
    }
}

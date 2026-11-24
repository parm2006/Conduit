#include "packet_assembler.h"
#include "video_cursor_policy.h"
#include "latency_trace.h"

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
    m_pending_bytes = 0;
    m_latest_completed_frame = 0;
}

void PacketAssembler::IngestFragment(const PacketHeader& header, const uint8_t* payload, size_t payload_len) {
    constexpr size_t max_bytes = 16 * 1024 * 1024;
    constexpr size_t max_frames = 32;
    constexpr size_t max_fragments = (max_bytes + STREAMER_MAX_PAYLOAD_SIZE - 1) / STREAMER_MAX_PAYLOAD_SIZE;
    if (!payload || !payload_len || payload_len > STREAMER_MAX_PAYLOAD_SIZE || !header.total_fragments ||
        header.total_fragments > max_fragments || header.fragment_index >= header.total_fragments) return;
    std::unique_lock<std::mutex> lock(m_mutex);
    if (m_latest_completed_frame && static_cast<int32_t>(header.frame_id - m_latest_completed_frame) <= 0) return;
    bool request_keyframe = false;
    auto notify = [&] {
        auto callback = m_on_keyframe_needed;
        lock.unlock();
        if (request_keyframe && callback) callback();
    };
    if (!m_pending_frames.count(header.frame_id)) {
        while (m_pending_frames.size() >= max_frames) {
            auto oldest = m_pending_frames.begin();
            request_keyframe = request_keyframe || oldest->second.is_keyframe;
            m_pending_bytes -= oldest->second.bytes;
            m_pending_frames.erase(oldest);
        }
    }
    auto& pending = m_pending_frames[header.frame_id];
    const bool keyframe = (header.flags & STREAMER_FLAG_KEYFRAME) != 0;
    const uint8_t cursor_flags = header.flags & (STREAMER_FLAG_CURSOR_KNOWN | STREAMER_FLAG_CURSOR_IN_FRAME);
    if (pending.fragments.empty()) {
        pending.frame_id = header.frame_id; pending.timestamp_ms = header.timestamp_ms;
        pending.total_fragments = header.total_fragments; pending.received_fragments = 0;
        pending.is_keyframe = keyframe; pending.first_rx_us = conduit::QpcNowUs(); pending.bytes = 0;
        pending.cursor_flags = cursor_flags;
    } else if (pending.total_fragments != header.total_fragments || pending.timestamp_ms != header.timestamp_ms ||
               pending.is_keyframe != keyframe || pending.cursor_flags != cursor_flags) {
        m_pending_bytes -= pending.bytes; m_pending_frames.erase(header.frame_id);
        request_keyframe = true; notify(); return;
    }
    if (!pending.fragments.count(header.fragment_index)) {
        if (payload_len > max_bytes - m_pending_bytes) {
            m_pending_bytes -= pending.bytes; m_pending_frames.erase(header.frame_id);
            request_keyframe = true; notify(); return;
        }
        pending.fragments[header.fragment_index].assign(payload, payload + payload_len);
        pending.bytes += payload_len; m_pending_bytes += payload_len; ++pending.received_fragments;
    }
    if (pending.received_fragments != pending.total_fragments) { notify(); return; }
    AssembledFrame frame;
    frame.data.reserve(pending.bytes);
    for (const auto& fragment : pending.fragments)
        frame.data.insert(frame.data.end(), fragment.second.begin(), fragment.second.end());
    frame.frame_id = pending.frame_id; frame.timestamp_ms = pending.timestamp_ms;
    frame.is_keyframe = pending.is_keyframe; frame.first_rx_us = pending.first_rx_us;
    frame.cursor_in_frame = conduit::CursorPolicyFromFlags(pending.cursor_flags);
    frame.complete_us = conduit::QpcNowUs();
    m_latest_completed_frame = frame.frame_id;
    auto it = m_pending_frames.begin();
    while (it != m_pending_frames.end()) {
        if (static_cast<int32_t>(it->first - frame.frame_id) <= 0) {
            m_pending_bytes -= it->second.bytes; it = m_pending_frames.erase(it);
        } else ++it;
    }
    auto callback = m_on_frame;
    notify();
    if (callback) callback(std::move(frame));
}

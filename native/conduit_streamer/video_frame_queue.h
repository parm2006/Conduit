#ifndef CONDUIT_VIDEO_FRAME_QUEUE_H
#define CONDUIT_VIDEO_FRAME_QUEUE_H

#include "packet_assembler.h"
#include <chrono>
#include <condition_variable>
#include <deque>

// A bounded handoff to the decoder. Compressed H.264 frames cannot be replaced
// independently: after dropping a reference, resume only at a keyframe.
class VideoFrameQueue {
public:
    using Clock = std::chrono::steady_clock;
    enum class PushResult { Accepted, Dropped, RequestKeyframe };

    explicit VideoFrameQueue(size_t max_frames = 3, size_t max_bytes = 16 * 1024 * 1024)
        : m_max_frames(max_frames), m_max_bytes(max_bytes) {}

    void Reset() {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_frames.clear();
        m_bytes = 0;
        m_stopped = false;
        m_waiting_keyframe = true;
        m_requested = false;
        m_have_frame = false;
    }

    PushResult Push(AssembledFrame&& frame, Clock::time_point now = Clock::now()) {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_stopped) return PushResult::Dropped;
        const bool missing_reference = m_have_frame && frame.frame_id != m_last_frame + 1;
        if (missing_reference || frame.data.size() > m_max_bytes || m_frames.size() >= m_max_frames ||
            frame.data.size() > m_max_bytes - m_bytes) {
            m_frames.clear();
            m_bytes = 0;
            m_waiting_keyframe = true;
        }
        if (frame.data.size() <= m_max_bytes && (!m_waiting_keyframe || frame.is_keyframe)) {
            m_waiting_keyframe = false;
            m_last_frame = frame.frame_id;
            m_have_frame = true;
            m_bytes += frame.data.size();
            m_frames.push_back(std::move(frame));
            m_ready.notify_one();
            return PushResult::Accepted;
        }
        if (RequestKeyframeLocked(now)) return PushResult::RequestKeyframe;
        return PushResult::Dropped;
    }

    bool RequestKeyframe(Clock::time_point now = Clock::now()) {
        std::lock_guard<std::mutex> lock(m_mutex);
        return !m_stopped && RequestKeyframeLocked(now);
    }

    bool WaitPop(AssembledFrame& frame) {
        std::unique_lock<std::mutex> lock(m_mutex);
        m_ready.wait(lock, [this] { return m_stopped || !m_frames.empty(); });
        if (m_stopped) return false;
        m_bytes -= m_frames.front().data.size();
        frame = std::move(m_frames.front());
        m_frames.pop_front();
        return true;
    }

    void Stop() {
        std::lock_guard<std::mutex> lock(m_mutex);
        m_stopped = true;
        m_frames.clear();
        m_bytes = 0;
        m_ready.notify_all();
    }

private:
    bool RequestKeyframeLocked(Clock::time_point now) {
        if (m_requested && now - m_last_request < std::chrono::milliseconds(250)) return false;
        m_requested = true;
        m_last_request = now;
        return true;
    }

    const size_t m_max_frames;
    const size_t m_max_bytes;
    std::mutex m_mutex;
    std::condition_variable m_ready;
    std::deque<AssembledFrame> m_frames;
    size_t m_bytes = 0;
    bool m_stopped = false;
    bool m_waiting_keyframe = true;
    bool m_requested = false;
    bool m_have_frame = false;
    uint32_t m_last_frame = 0;
    Clock::time_point m_last_request;
};

#endif

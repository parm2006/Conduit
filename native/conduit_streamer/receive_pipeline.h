// Adapted from the user-supplied Claude Stage 1 implementation. Network ingress
// is bounded; initialization, decode, presentation and teardown share one worker.
#pragma once
#include "latency_trace.h"
#include <objbase.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace conduit {
struct EncodedFrame {
    std::vector<uint8_t> data;
    uint32_t frame_id = 0;
    bool keyframe = false;
    uint32_t sender_ts_ms = 0;
    uint64_t first_rx_us = 0;
    uint64_t complete_us = 0;
};
struct PipelineConfig {
    size_t max_queue_depth = 3;
    size_t max_queue_bytes = 16 * 1024 * 1024;
    uint64_t max_queue_age_us = 100000;
    uint64_t keyframe_request_interval_us = 250000;
    uint64_t max_present_gap_us = 33000;
    // Zero disables this heuristic: NeedMoreInput is normal decoder backpressure.
    uint32_t max_no_output_inputs = 0;
};

class FrameQueue {
public:
    struct Stats {
        uint64_t pushed = 0, delivered = 0;
        uint64_t dropped_stale = 0, dropped_awaiting_key = 0, dropped_overflow = 0;
        uint64_t dropped_superseded = 0, dropped_age = 0, dropped_loss = 0, dropped_stop = 0;
        uint64_t gaps = 0, keyframe_requests = 0;
    };
    explicit FrameQueue(PipelineConfig cfg = {}) : cfg_(cfg) {
        if (!cfg.max_queue_depth || !cfg.max_queue_bytes || !cfg.keyframe_request_interval_us)
            throw std::invalid_argument("positive queue bounds and request interval required");
    }
    void Reset() {
        std::lock_guard<std::mutex> lock(m_);
        q_.clear(); bytes_ = 0; awaiting_key_ = true;
        have_last_ = false; last_id_ = 0; last_req_valid_ = false; last_req_ = 0;
        stats_ = Stats{};
    }
    void DiscardStopped() {
        std::lock_guard<std::mutex> lock(m_);
        Flush(stats_.dropped_stop);
    }
    bool Push(EncodedFrame&& f, uint64_t now_us) {
        std::lock_guard<std::mutex> lock(m_);
        ++stats_.pushed;
        bool gap = false;
        if (have_last_) {
            const uint32_t delta = f.frame_id - last_id_;
            if (!delta || delta > 0x7fffffffU) { ++stats_.dropped_stale; return false; }
            gap = delta != 1;
        }
        have_last_ = true; last_id_ = f.frame_id;
        if (f.data.empty() || f.data.size() > cfg_.max_queue_bytes) {
            Flush(stats_.dropped_overflow); ++stats_.dropped_overflow;
            awaiting_key_ = true; return WantKey(now_us);
        }
        if (TooOld(f, now_us)) {
            Flush(stats_.dropped_age); ++stats_.dropped_age;
            awaiting_key_ = true; return WantKey(now_us);
        }
        if (f.keyframe) {
            Flush(stats_.dropped_superseded);
            awaiting_key_ = false; bytes_ = f.data.size();
            q_.push_back(std::move(f)); return false;
        }
        if (gap) {
            ++stats_.gaps;
            if (!awaiting_key_) { Flush(stats_.dropped_loss); awaiting_key_ = true; }
        }
        if (awaiting_key_) { ++stats_.dropped_awaiting_key; return WantKey(now_us); }
        if (!q_.empty() && TooOld(q_.front(), now_us)) {
            Flush(stats_.dropped_age); ++stats_.dropped_age;
            awaiting_key_ = true; return WantKey(now_us);
        }
        if (q_.size() >= cfg_.max_queue_depth || f.data.size() > cfg_.max_queue_bytes - bytes_) {
            Flush(stats_.dropped_overflow); ++stats_.dropped_overflow;
            awaiting_key_ = true; return WantKey(now_us);
        }
        bytes_ += f.data.size(); q_.push_back(std::move(f)); return false;
    }
    bool TryPop(EncodedFrame* out, uint64_t now_us = QpcNowUs()) {
        std::lock_guard<std::mutex> lock(m_);
        if (!out || q_.empty()) return false;
        // Recheck when consuming: an idle queue can age without another Push.
        if (TooOld(q_.front(), now_us)) {
            Flush(stats_.dropped_age); awaiting_key_ = true; return false;
        }
        bytes_ -= q_.front().data.size();
        *out = std::move(q_.front()); q_.pop_front(); ++stats_.delivered; return true;
    }
    bool ReportDecodeFailure(uint64_t now_us) {
        std::lock_guard<std::mutex> lock(m_);
        Flush(stats_.dropped_loss); awaiting_key_ = true; return WantKey(now_us);
    }
    bool PollRerequest(uint64_t now_us) {
        std::lock_guard<std::mutex> lock(m_);
        return awaiting_key_ && WantKey(now_us);
    }
    size_t Size() const { std::lock_guard<std::mutex> lock(m_); return q_.size(); }
    size_t Bytes() const { std::lock_guard<std::mutex> lock(m_); return bytes_; }
    bool AwaitingKeyframe() const { std::lock_guard<std::mutex> lock(m_); return awaiting_key_; }
    Stats GetStats() const { std::lock_guard<std::mutex> lock(m_); return stats_; }
private:
    bool TooOld(const EncodedFrame& f, uint64_t now) const {
        return now > f.complete_us && now - f.complete_us > cfg_.max_queue_age_us;
    }
    void Flush(uint64_t& counter) { counter += q_.size(); q_.clear(); bytes_ = 0; }
    bool WantKey(uint64_t now) {
        if (last_req_valid_ && now >= last_req_ && now - last_req_ < cfg_.keyframe_request_interval_us) return false;
        last_req_ = now; last_req_valid_ = true; ++stats_.keyframe_requests; return true;
    }
    PipelineConfig cfg_;
    mutable std::mutex m_;
    std::deque<EncodedFrame> q_;
    size_t bytes_ = 0;
    bool awaiting_key_ = true, have_last_ = false, last_req_valid_ = false;
    uint32_t last_id_ = 0;
    uint64_t last_req_ = 0;
    Stats stats_;
};

enum class DecodeStatus { Ok, NeedMoreInput, Failed };
struct DecodedFrame {
    std::shared_ptr<void> handle;
    uint32_t width = 0, height = 0;
    // Decoder output may belong to an earlier input. Do not attribute the current
    // input's timestamps unless the adapter matched the output sample correctly.
    bool source_timing_known = false;
    uint64_t first_rx_us = 0, complete_us = 0;
};
struct PipelineCallbacks {
    // All callbacks except request_keyframe run on the COM MTA worker. The
    // request callback also runs from Submit and MUST enqueue without blocking.
    std::function<bool()> initialize;
    std::function<void()> shutdown;
    std::function<void()> tick;
    std::function<DecodeStatus(const EncodedFrame&, DecodedFrame*)> decode;
    std::function<bool(const DecodedFrame&)> present;
    std::function<void()> request_keyframe;
    std::function<void()> first_frame;
    std::function<void(const char*)> fatal; // must not destroy pipeline in callback
};

class ReceivePipeline {
public:
    ReceivePipeline(PipelineConfig cfg, PipelineCallbacks cb)
        : cfg_(cfg), cb_(std::move(cb)), queue_(cfg) {}
    ~ReceivePipeline() { Stop(); }
    ReceivePipeline(const ReceivePipeline&) = delete;
    ReceivePipeline& operator=(const ReceivePipeline&) = delete;
    bool Start() {
        std::lock_guard<std::mutex> lifecycle(lifecycle_m_);
        if (thread_.joinable()) return running_.load();
        {
            std::lock_guard<std::mutex> lock(cv_m_);
            queue_.Reset(); metrics_.Reset(); presented_ = 0; skipped_present_ = 0;
            ready_ = false; startup_ok_ = false; accepting_ = false; running_ = true;
        }
        try { thread_ = std::thread([this] { Run(); }); }
        catch (...) { running_ = false; return false; }
        std::unique_lock<std::mutex> lock(cv_m_);
        cv_.wait(lock, [this] { return ready_; });
        return startup_ok_;
    }
    void Stop() {
        std::lock_guard<std::mutex> lifecycle(lifecycle_m_);
        { std::lock_guard<std::mutex> lock(cv_m_); accepting_ = false; running_ = false; }
        cv_.notify_all();
        if (thread_.joinable() && thread_.get_id() != std::this_thread::get_id()) thread_.join();
        queue_.DiscardStopped();
    }
    bool Submit(EncodedFrame&& f) {
        bool request;
        {
            std::lock_guard<std::mutex> lock(cv_m_);
            if (!accepting_ || !running_.load()) return false;
            metrics_.reassembly.Add(f.complete_us >= f.first_rx_us ? f.complete_us - f.first_rx_us : 0);
            request = queue_.Push(std::move(f), QpcNowUs());
        }
        cv_.notify_one();
        // User callbacks cannot unwind through the UDP receive loop.
        if (request) RequestKeyframe();
        return true;
    }
    uint64_t presented_count() const { return presented_.load(); }
    uint64_t skipped_present_count() const { return skipped_present_.load(); }
    FrameQueue::Stats queue_stats() const { return queue_.GetStats(); }
    std::string Report() const {
        std::string s = metrics_.reassembly.Format("reassembly (first_rx->complete)");
        s += metrics_.queue_wait.Format("queue wait (complete->pop)");
        s += metrics_.decode.Format("decode call");
        s += metrics_.present.Format("present call (not scanout latency)");
        s += metrics_.complete_to_present.Format("complete->present return");
        s += metrics_.first_rx_to_present.Format("first_rx->present return");
        const auto q = queue_.GetStats();
        char b[512];
        std::snprintf(b, sizeof b, "presented=%llu skipped_present=%llu pushed=%llu gaps=%llu drop_overflow=%llu "
                      "drop_loss=%llu drop_age=%llu drop_await_key=%llu superseded=%llu stale=%llu "
                      "kf_requests=%llu depth=%zu bytes=%zu\n",
                      (unsigned long long)presented_.load(), (unsigned long long)skipped_present_.load(),
                      (unsigned long long)q.pushed, (unsigned long long)q.gaps,
                      (unsigned long long)q.dropped_overflow, (unsigned long long)q.dropped_loss,
                      (unsigned long long)q.dropped_age, (unsigned long long)q.dropped_awaiting_key,
                      (unsigned long long)q.dropped_superseded, (unsigned long long)q.dropped_stale,
                      (unsigned long long)q.keyframe_requests, queue_.Size(), queue_.Bytes());
        return s + b;
    }
private:
    struct Metrics {
        StageStats reassembly, queue_wait, decode, present, complete_to_present, first_rx_to_present;
        void Reset() { reassembly.Reset(); queue_wait.Reset(); decode.Reset(); present.Reset();
                       complete_to_present.Reset(); first_rx_to_present.Reset(); }
    };
    void Fatal(const char* message) noexcept {
        try { if (cb_.fatal) cb_.fatal(message); } catch (...) {}
    }
    void RequestKeyframe() noexcept {
        try { if (cb_.request_keyframe) cb_.request_keyframe(); } catch (...) { Fatal("keyframe callback failed"); }
    }
    void PublishStartup(bool success) {
        { std::lock_guard<std::mutex> lock(cv_m_); ready_ = true; startup_ok_ = success;
          accepting_ = success && running_.load(); }
        cv_.notify_all();
    }
    void Run() noexcept {
        const HRESULT com = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        bool init_attempted = false;
        try {
            if (FAILED(com)) { Fatal("COM MTA initialization failed"); PublishStartup(false); }
            else {
                init_attempted = true;
                const bool initialized = !cb_.initialize || cb_.initialize();
                if (!initialized) { Fatal("pipeline initialization failed"); PublishStartup(false); }
                else { PublishStartup(true); Consume(); }
            }
        } catch (...) { Fatal("pipeline callback threw an exception"); PublishStartup(false); }
        // Partial initialization also needs cleanup, on the same worker.
        if (init_attempted && cb_.shutdown) {
            try { cb_.shutdown(); } catch (...) { Fatal("pipeline shutdown failed"); }
        }
        { std::lock_guard<std::mutex> lock(cv_m_); accepting_ = false; running_ = false; }
        cv_.notify_all();
        if (SUCCEEDED(com)) CoUninitialize();
    }
    void Consume() {
        uint32_t no_output = 0;
        uint64_t last_present_us = 0;
        DecodedFrame last_presented; // retain decoder surface until its replacement is presented
        while (running_.load()) {
            {
                std::unique_lock<std::mutex> lock(cv_m_);
                cv_.wait_for(lock, std::chrono::milliseconds(25),
                             [this] { return !running_.load() || queue_.Size() > 0; });
            }
            if (!running_.load()) break;
            if (cb_.tick) cb_.tick();
            EncodedFrame f;
            const uint64_t t_pop = QpcNowUs();
            if (!queue_.TryPop(&f, t_pop)) {
                if (queue_.PollRerequest(t_pop)) RequestKeyframe();
                continue;
            }
            metrics_.queue_wait.Add(t_pop >= f.complete_us ? t_pop - f.complete_us : 0);
            DecodedFrame d;
            DecodeStatus status = cb_.decode ? cb_.decode(f, &d) : DecodeStatus::Failed;
            const uint64_t t_dec = QpcNowUs();
            metrics_.decode.Add(t_dec - t_pop);
            if (status == DecodeStatus::NeedMoreInput) {
                ++no_output;
                if (!cfg_.max_no_output_inputs || no_output <= cfg_.max_no_output_inputs) continue;
                status = DecodeStatus::Failed;
            }
            if (status == DecodeStatus::Failed) {
                no_output = 0;
                if (queue_.ReportDecodeFailure(t_dec)) RequestKeyframe();
                continue;
            }
            no_output = 0;
            if (queue_.Size() > 0 && last_present_us && t_dec - last_present_us < cfg_.max_present_gap_us) {
                ++skipped_present_; continue;
            }
            const bool ok = cb_.present ? cb_.present(d) : true;
            const uint64_t t_present = QpcNowUs();
            metrics_.present.Add(t_present - t_dec);
            if (d.source_timing_known) {
                if (t_present >= d.complete_us) metrics_.complete_to_present.Add(t_present - d.complete_us);
                if (t_present >= d.first_rx_us) metrics_.first_rx_to_present.Add(t_present - d.first_rx_us);
            }
            last_present_us = t_present;
            if (!ok) { Fatal("present failed (device lost?)"); break; }
            last_presented = std::move(d);
            const bool first = presented_.load() == 0;
            if (first && cb_.first_frame) cb_.first_frame();
            ++presented_;
        }
    }
    PipelineConfig cfg_;
    PipelineCallbacks cb_;
    FrameQueue queue_;
    Metrics metrics_;
    std::mutex lifecycle_m_, cv_m_;
    std::condition_variable cv_;
    bool ready_ = false, startup_ok_ = false, accepting_ = false;
    std::atomic<bool> running_{false};
    std::atomic<uint64_t> presented_{0}, skipped_present_{0};
    std::thread thread_;
};
} // namespace conduit

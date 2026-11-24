// latency_trace.h - monotonic microsecond clock, per-stage percentile stats,
// and an NTP-style cross-machine clock offset estimator.
#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <mutex>
#include <string>
#include <vector>

namespace conduit {

// QueryPerformanceCounter in microseconds. Only comparable on the SAME machine.
inline uint64_t QpcNowUs() {
    static const LONGLONG freq = [] {
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        return f.QuadPart;
    }();
    LARGE_INTEGER c;
    QueryPerformanceCounter(&c);
    return static_cast<uint64_t>(c.QuadPart / freq) * 1000000ull +
           static_cast<uint64_t>(c.QuadPart % freq) * 1000000ull / static_cast<uint64_t>(freq);
}

// Fixed-size ring of recent samples (microseconds). Cheap enough for 60 Hz use.
class StageStats {
public:
    struct Summary {
        size_t count = 0;
        uint64_t p50 = 0, p95 = 0, p99 = 0, max = 0;
    };

    explicit StageStats(size_t capacity = 2048) : ring_(capacity ? capacity : 1, 0) {}

    void Add(uint64_t us) {
        std::lock_guard<std::mutex> g(m_);
        ring_[static_cast<size_t>(total_ % ring_.size())] = us;
        ++total_;
    }

    void Reset() { std::lock_guard<std::mutex> g(m_); total_ = 0; }

    Summary Snapshot() const {
        std::vector<uint64_t> v;
        {
            std::lock_guard<std::mutex> g(m_);
            const size_t n = static_cast<size_t>(std::min<uint64_t>(total_, ring_.size()));
            v.assign(ring_.begin(), ring_.begin() + static_cast<std::ptrdiff_t>(n));
        }
        Summary s;
        s.count = v.size();
        if (v.empty()) return s;
        std::sort(v.begin(), v.end());
        auto at = [&](double q) {
            return v[(std::min)(v.size() - 1, static_cast<size_t>(q * static_cast<double>(v.size() - 1) + 0.5))];
        };
        s.p50 = at(0.50);
        s.p95 = at(0.95);
        s.p99 = at(0.99);
        s.max = v.back();
        return s;
    }

    std::string Format(const char* name) const {
        const Summary s = Snapshot();
        char b[192];
        std::snprintf(b, sizeof b, "%-34s n=%-5zu p50=%.2fms p95=%.2fms p99=%.2fms max=%.2fms\n", name,
                      s.count, s.p50 / 1000.0, s.p95 / 1000.0, s.p99 / 1000.0, s.max / 1000.0);
        return b;
    }

private:
    mutable std::mutex m_;
    std::vector<uint64_t> ring_;
    uint64_t total_ = 0;
};

// Estimates (remote_clock - local_clock) from ping/pong exchanges over the control
// channel. The lowest-RTT sample wins; its error bound is +/- RTT/2 (path asymmetry
// cannot be removed in software, so quote that bound with any cross-machine number).
//   t0: requester send  (local clock)    t1: responder receive (remote clock)
//   t2: responder send  (remote clock)   t3: requester receive (local clock)
class ClockOffsetEstimator {
public:
    void AddSample(uint64_t t0, uint64_t t1, uint64_t t2, uint64_t t3) {
        const int64_t rtt = static_cast<int64_t>(t3 - t0) - static_cast<int64_t>(t2 - t1);
        if (rtt < 0) return;
        const int64_t off = (static_cast<int64_t>(t1 - t0) + static_cast<int64_t>(t2 - t3)) / 2;
        samples_.push_back({rtt, off});
        if (samples_.size() > kMaxSamples) samples_.erase(samples_.begin());
    }
    bool Valid() const { return !samples_.empty(); }
    int64_t OffsetUs() const { return Best().off; }   // remote - local
    int64_t RttUs() const { return Best().rtt; }
    int64_t ErrorBoundUs() const { return Best().rtt / 2; }

private:
    struct Sample { int64_t rtt, off; };
    static constexpr size_t kMaxSamples = 32;
    Sample Best() const {
        Sample b{0, 0};
        bool first = true;
        for (const auto& s : samples_)
            if (first || s.rtt < b.rtt) { b = s; first = false; }
        return b;
    }
    std::vector<Sample> samples_;
};

}  // namespace conduit

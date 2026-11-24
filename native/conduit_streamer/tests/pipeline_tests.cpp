// pipeline_tests.cpp - GPU-free tests for the queue policy, scheduler, clock
// estimator and the pipeline thread.
// Build/run (from repo root):
//   cl /std:c++17 /EHsc /W4 /I native\conduit_streamer native\conduit_streamer\tests\pipeline_tests.cpp ole32.lib
//   pipeline_tests.exe
#include <windows.h> // match main.cpp include order (Windows min/max macros may already exist)
#include "receive_pipeline.h"
#include "send_scheduler.h"

#include <chrono>
#include <cstdio>
#include <thread>
#include <stdexcept>

using namespace conduit;

static std::atomic<int> g_fail{0};
#define CHECK(c)                                                         \
    do {                                                                 \
        if (!(c)) { std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); ++g_fail; } \
    } while (0)

static EncodedFrame mk(uint32_t id, bool key, uint64_t t) {
    EncodedFrame f;
    f.frame_id = id;
    f.keyframe = key;
    f.first_rx_us = f.complete_us = t;
    f.data.assign(16, 0);
    return f;
}

template <class F>
static bool WaitFor(F f, int ms = 3000) {
    const auto end = std::chrono::steady_clock::now() + std::chrono::milliseconds(ms);
    while (!f()) {
        if (std::chrono::steady_clock::now() > end) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return true;
}

static void TestInOrder() {
    FrameQueue q;
    EncodedFrame f;
    CHECK(!q.Push(mk(1, true, 0), 0));
    CHECK(!q.Push(mk(2, false, 0), 0));
    CHECK(!q.Push(mk(3, false, 0), 0));
    CHECK(q.Size() == 3);
    for (uint32_t id = 1; id <= 3; ++id) { CHECK(q.TryPop(&f, 0)); CHECK(f.frame_id == id); }
    CHECK(!q.TryPop(&f, 0));
}

static void TestFirstPFrameRequestsKeyframe() {
    FrameQueue q;
    CHECK(q.Push(mk(1, false, 0), 0));
    CHECK(q.AwaitingKeyframe());
    CHECK(q.Size() == 0);
}

static void TestGapRecovery() {
    FrameQueue q;
    EncodedFrame f;
    q.Push(mk(1, true, 0), 0);  q.TryPop(&f, 0);
    q.Push(mk(2, false, 0), 0); q.TryPop(&f, 0);
    CHECK(q.Push(mk(4, false, 1000), 1000));     // id 3 lost -> request IDR
    CHECK(q.AwaitingKeyframe());
    CHECK(q.Size() == 0);
    CHECK(!q.Push(mk(5, false, 2000), 2000));    // dropped, request rate-limited
    CHECK(q.Size() == 0);
    CHECK(!q.PollRerequest(100000));             // too soon
    CHECK(q.PollRerequest(251000));              // request may have been lost: ask again
    CHECK(!q.Push(mk(6, true, 300000), 300000)); // IDR resumes
    CHECK(!q.AwaitingKeyframe());
    CHECK(q.Size() == 1);
}

static void TestKeyframeSupersedes() {
    FrameQueue q;
    EncodedFrame f;
    q.Push(mk(1, true, 0), 0); q.Push(mk(2, false, 0), 0); q.Push(mk(3, false, 0), 0);
    CHECK(!q.Push(mk(4, true, 0), 0));
    CHECK(q.Size() == 1);
    CHECK(q.TryPop(&f, 0) && f.frame_id == 4);
    CHECK(q.GetStats().dropped_superseded == 3);
}

static void TestOverflowAndAgeAndStale() {
    {
        FrameQueue q;
        q.Push(mk(1, true, 0), 0); q.Push(mk(2, false, 0), 0); q.Push(mk(3, false, 0), 0);
        CHECK(q.Push(mk(4, false, 0), 0));  // depth 3 exceeded
        CHECK(q.Size() == 0 && q.AwaitingKeyframe());
    }
    {
        PipelineConfig c;
        c.max_queue_age_us = 100000;
        FrameQueue q(c);
        q.Push(mk(1, true, 0), 0);
        CHECK(q.Push(mk(2, false, 200000), 200000));  // head frame is 200 ms old
        CHECK(q.Size() == 0);
    }
    {
        FrameQueue q;
        q.Push(mk(5, true, 0), 0);
        CHECK(!q.Push(mk(3, false, 0), 0));
        CHECK(q.GetStats().dropped_stale == 1 && q.Size() == 1);
    }
}

static void TestScheduler() {
    SendScheduler s(60);  // min interval 16666 us
    s.OnCapture(true);
    CHECK(s.Poll(0) == SendScheduler::Action::Encode);
    s.OnEncoded(0);
    s.OnCapture(true);  // change inside the min interval
    CHECK(s.Poll(5000) == SendScheduler::Action::Wait);
    CHECK(s.WaitMs(5000) == 12);
    CHECK(s.Poll(16666) == SendScheduler::Action::Encode);  // trailing edge not lost
    s.OnEncoded(20000);
    CHECK(s.Poll(100000) == SendScheduler::Action::Wait);   // idle, not yet keepalive time
    CHECK(s.Poll(270000) == SendScheduler::Action::Keepalive);
    s.OnEncoded(270000);
    s.MarkDirty();                                          // keyframe request on idle desktop
    CHECK(s.Poll(271000) == SendScheduler::Action::Wait);
    CHECK(s.Poll(300000) == SendScheduler::Action::Encode);
}

static void TestClockOffset() {
    ClockOffsetEstimator e;
    const int64_t offset = 5000000;  // remote clock is 5 s ahead
    // 2 ms each way, 100 us processing
    const uint64_t t0 = 1000, t1 = t0 + 2000 + offset, t2 = t1 + 100, t3 = t0 + 2000 + 100 + 2000;
    e.AddSample(t0, t1, t2, t3);
    // a noisier, asymmetric sample must not win
    e.AddSample(10000, 10000 + 9000 + offset, 10000 + 9000 + offset + 100, 10000 + 9000 + 100 + 1000);
    CHECK(e.Valid());
    CHECK(e.OffsetUs() == offset);
    CHECK(e.RttUs() == 4000);
}

static void TestPipelineThread() {
    std::atomic<int> kf{0}, first{0};
    PipelineCallbacks cb;
    cb.decode = [](const EncodedFrame&, DecodedFrame* o) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        o->handle = std::make_shared<int>(1);
        o->width = 1920;
        o->height = 1080;
        return DecodeStatus::Ok;
    };
    cb.present = [](const DecodedFrame&) { return true; };
    cb.request_keyframe = [&] { ++kf; };
    cb.first_frame = [&] { ++first; };

    ReceivePipeline p(PipelineConfig{}, cb);
    p.Start();
    p.Submit(mk(1, true, QpcNowUs()));
    CHECK(WaitFor([&] { return p.presented_count() >= 1; }));
    CHECK(first == 1);

    p.Submit(mk(3, false, QpcNowUs()));  // id 2 never arrived
    CHECK(kf >= 1);
    p.Submit(mk(4, true, QpcNowUs()));
    CHECK(WaitFor([&] { return p.presented_count() >= 2; }));

    // Burst far faster than the 5 ms decoder: the queue must stay bounded.
    p.Submit(mk(10, true, QpcNowUs()));
    for (uint32_t id = 11; id <= 40; ++id) p.Submit(mk(id, false, QpcNowUs()));
    CHECK(p.queue_stats().dropped_overflow > 0);
    const uint64_t before = p.presented_count();
    p.Submit(mk(100, true, QpcNowUs()));
    CHECK(WaitFor([&] { return p.presented_count() > before; }));

    p.Stop();
    std::printf("%s", p.Report().c_str());
}


static void TestAgeAtPopAndByteBound() {
    PipelineConfig cfg;
    cfg.max_queue_age_us = 100;
    cfg.max_queue_bytes = 32;
    FrameQueue q(cfg);
    EncodedFrame f;
    q.Push(mk(1, true, 0), 0);
    CHECK(!q.TryPop(&f, 101));
    CHECK(q.AwaitingKeyframe() && q.Size() == 0);
    CHECK(q.GetStats().dropped_age == 1);
    auto oversized = mk(2, true, 200);
    oversized.data.resize(33);
    CHECK(q.Push(std::move(oversized), 200));
    CHECK(q.Size() == 0 && q.AwaitingKeyframe());
    q.Push(mk(3, true, 300), 300);
    q.Push(mk(4, false, 300), 300);
    CHECK(q.Bytes() == 32);
    q.Push(mk(5, false, 300), 300);
    CHECK(q.Bytes() == 0 && q.AwaitingKeyframe());
}

static void TestWorkerLifecycleAndExceptions() {
    std::atomic<int> initialized{0}, shutdown{0}, fatal{0};
    std::thread::id worker;
    PipelineCallbacks cb;
    cb.initialize = [&] { worker = std::this_thread::get_id(); ++initialized; return true; };
    cb.shutdown = [&] { CHECK(std::this_thread::get_id() == worker); ++shutdown; };
    cb.decode = [&](const EncodedFrame&, DecodedFrame*) -> DecodeStatus {
        CHECK(std::this_thread::get_id() == worker);
        throw std::runtime_error("test decode failure");
    };
    cb.fatal = [&](const char*) { ++fatal; };
    ReceivePipeline p(PipelineConfig{}, cb);
    CHECK(!p.Submit(mk(1, true, QpcNowUs())));
    CHECK(p.Start());
    CHECK(initialized == 1);
    CHECK(p.Submit(mk(1, true, QpcNowUs())));
    CHECK(WaitFor([&] { return shutdown.load() == 1; }));
    CHECK(fatal == 1);
    CHECK(!p.Submit(mk(2, true, QpcNowUs())));
    p.Stop();
    CHECK(p.Start());
    CHECK(initialized == 2);
    CHECK(p.queue_stats().pushed == 0);
    p.Stop();
    CHECK(shutdown == 2);
    CHECK(!p.Submit(mk(3, true, QpcNowUs())));
    PipelineCallbacks bad;
    bad.initialize = [] { return false; };
    bad.shutdown = [&] { ++shutdown; };
    ReceivePipeline fail(PipelineConfig{}, bad);
    CHECK(!fail.Start());
    fail.Stop();
    CHECK(shutdown == 3);
}

static void TestNeedMoreInputIsNotDecodeFailure() {
    std::atomic<int> calls{0}, requests{0};
    PipelineCallbacks cb;
    cb.decode = [&](const EncodedFrame&, DecodedFrame*) { ++calls; return DecodeStatus::NeedMoreInput; };
    cb.request_keyframe = [&] { ++requests; };
    ReceivePipeline p(PipelineConfig{}, cb);
    CHECK(p.Start());
    for (uint32_t id = 1; id <= 8; ++id) {
        CHECK(p.Submit(mk(id, id == 1, QpcNowUs())));
        CHECK(WaitFor([&] { return calls.load() == static_cast<int>(id); }));
    }
    CHECK(p.queue_stats().dropped_loss == 0);
    p.Stop();
}


static void TestPresentedSurfaceLifetime() {
    std::atomic<int> released{0};
    PipelineCallbacks cb;
    cb.decode = [&](const EncodedFrame&, DecodedFrame* out) {
        out->handle = std::shared_ptr<void>(new int(1), [&](void* p) { delete static_cast<int*>(p); ++released; });
        return DecodeStatus::Ok;
    };
    cb.present = [](const DecodedFrame&) { return true; };
    ReceivePipeline p(PipelineConfig{}, cb);
    CHECK(p.Start());
    p.Submit(mk(1, true, QpcNowUs()));
    CHECK(WaitFor([&] { return p.presented_count() == 1; }));
    CHECK(released == 0);
    p.Submit(mk(2, false, QpcNowUs()));
    CHECK(WaitFor([&] { return p.presented_count() == 2; }));
    CHECK(released == 1);
    p.Stop();
    CHECK(released == 2);
}

int main() {
    TestInOrder();
    TestFirstPFrameRequestsKeyframe();
    TestGapRecovery();
    TestKeyframeSupersedes();
    TestOverflowAndAgeAndStale();
    TestScheduler();
    TestClockOffset();
    TestPipelineThread();
    TestAgeAtPopAndByteBound();
    TestWorkerLifecycleAndExceptions();
    TestNeedMoreInputIsNotDecodeFailure();
    TestPresentedSurfaceLifetime();
    const int failures = g_fail.load();
    std::printf(failures ? "\n%d FAILURE(S)\n" : "\nALL PASSED\n", failures);
    return failures ? 1 : 0;
}

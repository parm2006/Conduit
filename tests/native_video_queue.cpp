#include "../native/conduit_streamer/video_frame_queue.h"
#include <cassert>
#include <future>

static AssembledFrame Frame(uint32_t id, bool key = false, size_t bytes = 4) {
    AssembledFrame f;
    f.frame_id = id;
    f.timestamp_ms = 0;
    f.is_keyframe = key;
    f.data.resize(bytes);
    return f;
}
int main() {
    using Result = VideoFrameQueue::PushResult;
    auto now = VideoFrameQueue::Clock::now();
    VideoFrameQueue queue(2, 16);
    AssembledFrame out;
    assert(queue.Push(Frame(1), now) == Result::RequestKeyframe);
    assert(queue.Push(Frame(2), now) == Result::Dropped);
    assert(queue.Push(Frame(3, true), now) == Result::Accepted);
    assert(queue.Push(Frame(4), now) == Result::Accepted);
    assert(queue.WaitPop(out) && out.frame_id == 3);
    assert(queue.WaitPop(out) && out.frame_id == 4);
    assert(queue.Push(Frame(5), now) == Result::Accepted);
    assert(queue.Push(Frame(6), now) == Result::Accepted);
    assert(queue.Push(Frame(7), now) == Result::Dropped); // overflow, request throttled
    assert(queue.Push(Frame(8), now + std::chrono::milliseconds(300)) == Result::RequestKeyframe);
    assert(queue.Push(Frame(9, true), now) == Result::Accepted);
    assert(queue.WaitPop(out) && out.frame_id == 9); // no dependent stale frames
    assert(queue.Push(Frame(10, true, 17), now) == Result::Dropped);
    assert(queue.Push(Frame(11, true), now) == Result::Accepted);
    queue.Stop();
    assert(!queue.WaitPop(out)); // stop discards queued work
    assert(queue.Push(Frame(12, true), now) == Result::Dropped);
    queue.Reset();
    auto waiter = std::async(std::launch::async, [&] { AssembledFrame f; return queue.WaitPop(f); });
    queue.Stop();
    assert(waiter.wait_for(std::chrono::seconds(1)) == std::future_status::ready);
    assert(!waiter.get());
    queue.Reset();
    assert(queue.Push(Frame(13), now) == Result::RequestKeyframe);
    assert(queue.Push(Frame(14, true), now) == Result::Accepted);
    assert(queue.WaitPop(out) && out.frame_id == 14);
    assert(queue.Push(Frame(16), now + std::chrono::seconds(1)) == Result::RequestKeyframe);
    assert(queue.Push(Frame(17), now + std::chrono::seconds(1)) == Result::Dropped);
    assert(queue.Push(Frame(18, true), now + std::chrono::seconds(1)) == Result::Accepted);
    assert(queue.WaitPop(out) && out.frame_id == 18);
    assert(queue.Push(Frame(19), now + std::chrono::seconds(1)) == Result::Accepted);
    assert(queue.Push(Frame(20), now + std::chrono::seconds(1)) == Result::Accepted);
    // A keyframe arriving at overflow can immediately replace the backlog.
    assert(queue.Push(Frame(21, true), now + std::chrono::seconds(1)) == Result::Accepted);
    assert(queue.WaitPop(out) && out.frame_id == 21);
    queue.Reset();
    assert(queue.RequestKeyframe(now));
    assert(!queue.RequestKeyframe(now));
    assert(queue.RequestKeyframe(now + std::chrono::milliseconds(250)));
    VideoFrameQueue bytes_queue(4, 8);
    assert(bytes_queue.Push(Frame(1, true), now) == Result::Accepted);
    assert(bytes_queue.Push(Frame(2), now) == Result::Accepted);
    assert(bytes_queue.Push(Frame(3), now) == Result::RequestKeyframe);
    assert(bytes_queue.Push(Frame(4, true), now) == Result::Accepted);
    assert(bytes_queue.WaitPop(out) && out.frame_id == 4);
}

// Real encrypted UDP roundtrips. No capture, codec, DLL, or desktop access.
#include <winsock2.h>
#include <ws2tcpip.h>
#include "udp_transport.h"
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <stdexcept>

using Datagram = std::vector<uint8_t>;
using Datagrams = std::vector<Datagram>;

static void Check(bool ok, const char* message) {
    if (!ok) throw std::runtime_error(message);
}

struct Socket {
    SOCKET handle = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    Socket() { Check(handle != INVALID_SOCKET, "socket creation failed"); }
    ~Socket() { closesocket(handle); }
    Socket(const Socket&) = delete;
    Socket& operator=(const Socket&) = delete;
    int BindLoopback() {
        sockaddr_in address = {};
        address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        Check(bind(handle, reinterpret_cast<sockaddr*>(&address), sizeof address) == 0,
              "ephemeral loopback bind failed");
        int length = sizeof address;
        Check(getsockname(handle, reinterpret_cast<sockaddr*>(&address), &length) == 0,
              "getsockname failed");
        return ntohs(address.sin_port);
    }
};

struct Observed {
    std::mutex mutex;
    std::condition_variable ready;
    std::vector<AssembledFrame> frames;
    void Receive(AssembledFrame&& frame) {
        std::lock_guard<std::mutex> lock(mutex);
        frames.push_back(std::move(frame));
        ready.notify_all();
    }
    bool WaitFor(size_t count, int milliseconds = 1000) {
        std::unique_lock<std::mutex> lock(mutex);
        return ready.wait_for(lock, std::chrono::milliseconds(milliseconds),
                              [&] { return frames.size() >= count; });
    }
    void Expect(size_t count, const EncodedPacket& expected) {
        std::lock_guard<std::mutex> lock(mutex);
        Check(frames.size() == count, "unexpected plaintext callback count");
        const auto& actual = frames.back();
        Check(actual.data == expected.data, "plaintext changed during encrypted transport");
        Check(actual.cursor_in_frame == expected.cursor_in_frame, "cursor policy changed");
        Check(actual.frame_id == expected.frame_id && actual.timestamp_ms == expected.timestamp_ms,
              "frame identity changed");
        Check(actual.is_keyframe == expected.is_keyframe, "keyframe flag changed");
    }
};

struct Transport {
    Socket proxy;
    UdpSender sender;
    Observed observed;
    UdpReceiver receiver; // Stops its callback thread before Observed is destroyed.
    int receiver_port = 0;
    Transport() {
        const uint8_t key[32] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
                                16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31};
        const int proxy_port = proxy.BindLoopback();
        // Receiver's public API takes a nonzero port. Release a fresh OS-selected
        // reservation immediately before binding; retry only this small race.
        for (int attempt = 0; attempt < 8; ++attempt) {
            { Socket reservation; receiver_port = reservation.BindLoopback(); }
            if (receiver.Initialize(receiver_port, key, sizeof key,
                    [this](AssembledFrame&& frame) { observed.Receive(std::move(frame)); }, [] {})) break;
            receiver_port = 0;
        }
        Check(receiver_port != 0 && receiver.Start(), "receiver failed to start");
        Check(sender.Initialize("127.0.0.1", proxy_port, key, sizeof key), "sender init failed");
    }
    Datagrams Capture(const EncodedPacket& frame) {
        Check(sender.SendFrame(frame), "real encrypted sender failed");
        const size_t count = (frame.data.size() + STREAMER_MAX_PAYLOAD_SIZE - 1) / STREAMER_MAX_PAYLOAD_SIZE;
        Datagrams datagrams;
        for (size_t index = 0; index < count; ++index) {
            fd_set readable;
            FD_ZERO(&readable); FD_SET(proxy.handle, &readable);
            timeval timeout = {1, 0};
            Check(select(0, &readable, nullptr, nullptr, &timeout) == 1, "encrypted capture timed out");
            Datagram datagram(2048);
            const int size = recv(proxy.handle, reinterpret_cast<char*>(datagram.data()),
                                  static_cast<int>(datagram.size()), 0);
            Check(size > static_cast<int>(sizeof(PacketHeader) + STREAMER_GCM_TAG_LEN),
                  "invalid encrypted datagram size");
            datagram.resize(static_cast<size_t>(size));
            datagrams.push_back(std::move(datagram));
        }
        Check(datagrams.size() >= 2, "probe must exercise fragmented frames");
        return datagrams;
    }
    void ForwardReversed(const Datagrams& datagrams) {
        sockaddr_in target = {};
        target.sin_family = AF_INET;
        target.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        target.sin_port = htons(static_cast<u_short>(receiver_port));
        for (auto it = datagrams.rbegin(); it != datagrams.rend(); ++it) {
            const int size = static_cast<int>(it->size());
            Check(sendto(proxy.handle, reinterpret_cast<const char*>(it->data()), size, 0,
                         reinterpret_cast<const sockaddr*>(&target), sizeof target) == size,
                  "loopback proxy forwarding failed");
        }
    }
};

static EncodedPacket Frame(uint32_t id, int cursor) {
    EncodedPacket frame = {};
    frame.frame_id = id; frame.timestamp_ms = id * 33; frame.is_keyframe = true;
    frame.cursor_in_frame = cursor;
    frame.data.resize(2 * STREAMER_MAX_PAYLOAD_SIZE + 137);
    for (size_t index = 0; index < frame.data.size(); ++index)
        frame.data[index] = static_cast<uint8_t>((index * 37 + id * 19) & 255);
    return frame;
}

static void Roundtrip() {
    Transport transport;
    size_t count = 0;
    for (int policy : {-1, 0, 1}) {
        const auto frame = Frame(static_cast<uint32_t>(++count), policy);
        transport.ForwardReversed(transport.Capture(frame));
        Check(transport.observed.WaitFor(count), "fragmented frame did not arrive");
        transport.observed.Expect(count, frame);
    }
    std::puts("roundtrip: reordered fragmented payloads and cursor modes -1/0/1 passed");
}

static void Tamper(bool header) {
    Transport transport;
    const auto baseline = Frame(1, 1);
    transport.ForwardReversed(transport.Capture(baseline));
    Check(transport.observed.WaitFor(1), "baseline frame did not arrive");
    transport.observed.Expect(1, baseline);
    auto damaged = transport.Capture(Frame(2, 0));
    if (header) {
        // Change all fragments consistently: assembler policy checks alone must
        // not explain rejection. Only the authenticated header prevents delivery.
        for (auto& datagram : damaged)
            datagram[offsetof(PacketHeader, flags)] ^= STREAMER_FLAG_CURSOR_IN_FRAME;
    } else {
        damaged.front()[sizeof(PacketHeader) + 11] ^= 0x80;
    }
    transport.ForwardReversed(damaged);
    Check(!transport.observed.WaitFor(2, 200), "tampered frame reached plaintext callback");
    transport.observed.Expect(1, baseline); // No plaintext or cursor state change.
    const auto recovery = Frame(3, 0);
    transport.ForwardReversed(transport.Capture(recovery));
    Check(transport.observed.WaitFor(2), "valid keyframe did not recover after tampering");
    transport.observed.Expect(2, recovery);
    const auto stats = transport.receiver.GetStats();
    Check(stats.find(header ? "auth_rejected=3" : "auth_rejected=1") != std::string::npos,
          "authentication rejection diagnostics do not match the damaged datagrams");
    std::puts(header ? "tamper_header: authenticated cursor flags rejected; recovery passed"
                     : "tamper_ciphertext: modified ciphertext rejected; recovery passed");
}

int main(int argc, char** argv) {
    try {
        WSADATA data = {};
        Check(WSAStartup(MAKEWORD(2, 2), &data) == 0, "WSAStartup failed");
        Check(argc == 2, "expected roundtrip, tamper_ciphertext, or tamper_header");
        if (std::strcmp(argv[1], "roundtrip") == 0) Roundtrip();
        else if (std::strcmp(argv[1], "tamper_ciphertext") == 0) Tamper(false);
        else if (std::strcmp(argv[1], "tamper_header") == 0) Tamper(true);
        else throw std::runtime_error("unknown probe mode");
        WSACleanup();
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "transport probe failed: %s (WSA=%d)\n", error.what(), WSAGetLastError());
        return 1;
    }
}

#ifndef CONDUIT_STREAMER_UDP_TRANSPORT_H
#define CONDUIT_STREAMER_UDP_TRANSPORT_H

#include "protocol.h"
#include "crypto_aead.h"
#include "packet_assembler.h"
#include "h264_encoder.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <thread>
#include <atomic>
#include <string>

#pragma comment(lib, "ws2_32.lib")

class UdpSender {
public:
    UdpSender();
    ~UdpSender();

    bool Initialize(const char* target_ip, int target_port, const uint8_t* key, size_t key_len);
    void Cleanup();

    bool SendFrame(const EncodedPacket& packet);

private:
    SOCKET m_socket;
    sockaddr_in m_dest_addr;
    CryptoAead m_crypto;
    uint32_t m_next_sequence;
    uint32_t m_session_salt;
    uint64_t m_packet_counter;
};

struct ReplayWindow {
    static constexpr uint32_t kWindowSize = 256;
    uint64_t m_bits[4] = {};
    uint32_t m_max_seq = 0;
    bool m_initialized = false;

    void Reset() {
        for (auto& word : m_bits) word = 0;
        m_max_seq = 0;
        m_initialized = false;
    }

    bool Accept(uint32_t seq) {
        if (!m_initialized) {
            Reset();
            m_initialized = true;
            m_max_seq = seq;
            m_bits[0] = 1ULL;
            return true;
        }
        if (seq > m_max_seq) {
            const uint32_t diff = seq - m_max_seq;
            if (diff >= kWindowSize) {
                for (auto& word : m_bits) word = 0;
            } else {
                const uint32_t word_shift = diff / 64;
                const uint32_t bit_shift = diff % 64;
                for (int i = 3; i >= 0; --i) {
                    uint64_t val = 0;
                    const int src = i - static_cast<int>(word_shift);
                    if (src >= 0) {
                        val = m_bits[src] << bit_shift;
                        if (bit_shift != 0 && src > 0) {
                            val |= m_bits[src - 1] >> (64 - bit_shift);
                        }
                    }
                    m_bits[i] = val;
                }
            }
            m_bits[0] |= 1ULL;
            m_max_seq = seq;
            return true;
        }
        const uint32_t diff = m_max_seq - seq;
        if (diff >= kWindowSize) return false;
        const uint64_t mask = 1ULL << (diff % 64);
        uint64_t& word = m_bits[diff / 64];
        if ((word & mask) != 0) return false;
        word |= mask;
        return true;
    }
};

class UdpReceiver {
public:
    UdpReceiver();
    ~UdpReceiver();

    bool Initialize(int listen_port, const uint8_t* key, size_t key_len,
                    FrameReadyCallback on_frame, KeyframeNeededCallback on_keyframe_needed);
    void Cleanup();

    bool Start();
    void Stop();
    std::string GetStats() const;

private:
    void ReceiveLoop();

    SOCKET m_socket;
    int m_listen_port;
    CryptoAead m_crypto;
    PacketAssembler m_assembler;
    std::thread m_thread;
    std::atomic<bool> m_running;
    ReplayWindow m_replay;
    std::atomic<uint64_t> m_packets{0}, m_authenticated{0}, m_auth_rejected{0}, m_replay_rejected{0};
};

#endif // CONDUIT_STREAMER_UDP_TRANSPORT_H

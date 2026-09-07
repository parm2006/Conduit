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

class UdpReceiver {
public:
    UdpReceiver();
    ~UdpReceiver();

    bool Initialize(int listen_port, const uint8_t* key, size_t key_len,
                    FrameReadyCallback on_frame, KeyframeNeededCallback on_keyframe_needed);
    void Cleanup();

    bool Start();
    void Stop();

private:
    void ReceiveLoop();

    SOCKET m_socket;
    int m_listen_port;
    CryptoAead m_crypto;
    PacketAssembler m_assembler;
    std::thread m_thread;
    std::atomic<bool> m_running;
    uint32_t m_max_sequence;
    uint64_t m_replay_window;
};

#endif // CONDUIT_STREAMER_UDP_TRANSPORT_H

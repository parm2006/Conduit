#include "udp_transport.h"

// ----------------------------------------------------------------------------
// UdpSender
// ----------------------------------------------------------------------------
UdpSender::UdpSender()
    : m_socket(INVALID_SOCKET)
    , m_next_sequence(1)
    , m_session_salt(0)
    , m_packet_counter(0)
{
    memset(&m_dest_addr, 0, sizeof(m_dest_addr));
}

UdpSender::~UdpSender() {
    Cleanup();
}

void UdpSender::Cleanup() {
    if (m_socket != INVALID_SOCKET) {
        closesocket(m_socket);
        m_socket = INVALID_SOCKET;
    }
    m_crypto.Cleanup();
    m_session_salt = 0;
    m_packet_counter = 0;
}

bool UdpSender::Initialize(const char* target_ip, int target_port, const uint8_t* key, size_t key_len) {
    Cleanup();
    if (!target_ip || target_port <= 0 || !key || key_len != 32) {
        return false;
    }

    WSADATA wsaData;
    WSAStartup(MAKEWORD(2, 2), &wsaData);

    if (!m_crypto.Initialize(key, key_len)) {
        return false;
    }

    // Generate fresh 32-bit CSPRNG session salt for NIST SP 800-38D deterministic nonce construction
    NTSTATUS rng_status = BCryptGenRandom(nullptr, reinterpret_cast<PUCHAR>(&m_session_salt),
                                          sizeof(m_session_salt), BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    if (!BCRYPT_SUCCESS(rng_status)) {
        m_session_salt = static_cast<uint32_t>(GetTickCount64() ^ reinterpret_cast<uintptr_t>(this));
    }
    m_packet_counter = 0;
    m_next_sequence = 1;

    m_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (m_socket == INVALID_SOCKET) {
        return false;
    }

    // Set high send buffer (1 MB) to absorb frame bursts
    int sndbuf = 1024 * 1024;
    setsockopt(m_socket, SOL_SOCKET, SO_SNDBUF, reinterpret_cast<const char*>(&sndbuf), sizeof(sndbuf));

    m_dest_addr.sin_family = AF_INET;
    m_dest_addr.sin_port = htons(static_cast<u_short>(target_port));
    if (inet_pton(AF_INET, target_ip, &m_dest_addr.sin_addr) <= 0) {
        Cleanup();
        return false;
    }

    return true;
}

bool UdpSender::SendFrame(const EncodedPacket& packet) {
    if (m_socket == INVALID_SOCKET || packet.data.empty()) {
        return false;
    }

    const size_t total_size = packet.data.size();
    const size_t chunk_size = STREAMER_MAX_PAYLOAD_SIZE;
    const uint16_t total_fragments = static_cast<uint16_t>((total_size + chunk_size - 1) / chunk_size);

    std::vector<uint8_t> buffer(sizeof(PacketHeader) + chunk_size + STREAMER_GCM_TAG_LEN);

    for (uint16_t frag = 0; frag < total_fragments; ++frag) {
        const size_t offset = frag * chunk_size;
        const size_t current_chunk = min(chunk_size, total_size - offset);

        PacketHeader header = {};
        header.magic = STREAMER_PACKET_MAGIC;
        header.version = STREAMER_PROTOCOL_VERSION;
        header.flags = 0;
        if (packet.is_keyframe) header.flags |= STREAMER_FLAG_KEYFRAME;
        if (frag == 0) header.flags |= STREAMER_FLAG_START_OF_FRAME;
        if (frag == total_fragments - 1) header.flags |= STREAMER_FLAG_END_OF_FRAME;

        header.sequence_number = m_next_sequence++;
        header.frame_id = packet.frame_id;
        header.fragment_index = frag;
        header.total_fragments = total_fragments;
        header.timestamp_ms = packet.timestamp_ms;
        header.payload_len = static_cast<uint16_t>(current_chunk);

        // Deterministic 12-byte (96-bit) AES-GCM Nonce (NIST SP 800-38D Section 8.2.1):
        // [0..3]: 32-bit CSPRNG session salt
        // [4..11]: 64-bit strictly monotonic packet counter (never repeats or wraps across 58M+ years)
        uint64_t packet_counter = ++m_packet_counter;
        memcpy(&header.iv[0], &m_session_salt, sizeof(m_session_salt));
        memcpy(&header.iv[4], &packet_counter, sizeof(packet_counter));

        // Copy header to buffer
        memcpy(buffer.data(), &header, sizeof(PacketHeader));

        // Encrypt payload directly into buffer after header
        uint8_t* ciphertext_dest = buffer.data() + sizeof(PacketHeader);
        uint8_t* auth_tag_dest = ciphertext_dest + current_chunk;

        // AAD is the entire packet header
        if (!m_crypto.Encrypt(packet.data.data() + offset, current_chunk,
                              buffer.data(), sizeof(PacketHeader),
                              header.iv, sizeof(header.iv),
                              ciphertext_dest,
                              auth_tag_dest, STREAMER_GCM_TAG_LEN)) {
            return false;
        }

        const int packet_size = static_cast<int>(sizeof(PacketHeader) + current_chunk + STREAMER_GCM_TAG_LEN);
        int sent = sendto(m_socket, reinterpret_cast<const char*>(buffer.data()), packet_size, 0,
                          reinterpret_cast<const sockaddr*>(&m_dest_addr), sizeof(m_dest_addr));
        if (sent <= 0) {
            return false;
        }
    }

    return true;
}

// ----------------------------------------------------------------------------
// UdpReceiver
// ----------------------------------------------------------------------------
UdpReceiver::UdpReceiver()
    : m_socket(INVALID_SOCKET)
    , m_listen_port(0)
    , m_running(false)
    , m_max_sequence(0)
    , m_replay_window(0)
{
}

UdpReceiver::~UdpReceiver() {
    Stop();
    Cleanup();
}

void UdpReceiver::Cleanup() {
    if (m_socket != INVALID_SOCKET) {
        closesocket(m_socket);
        m_socket = INVALID_SOCKET;
    }
    m_crypto.Cleanup();
    m_assembler.Reset();
    m_max_sequence = 0;
    m_replay_window = 0;
}

bool UdpReceiver::Initialize(int listen_port, const uint8_t* key, size_t key_len,
                             FrameReadyCallback on_frame, KeyframeNeededCallback on_keyframe_needed) {
    Stop();
    Cleanup();
    if (listen_port <= 0 || !key || key_len != 32) {
        return false;
    }

    WSADATA wsaData;
    WSAStartup(MAKEWORD(2, 2), &wsaData);

    if (!m_crypto.Initialize(key, key_len)) {
        return false;
    }

    m_assembler.Initialize(std::move(on_frame), std::move(on_keyframe_needed));
    m_listen_port = listen_port;
    m_max_sequence = 0;
    m_replay_window = 0;

    m_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (m_socket == INVALID_SOCKET) {
        return false;
    }

    // Set large receive buffer (2 MB) to absorb bursts
    int rcvbuf = 2 * 1024 * 1024;
    setsockopt(m_socket, SOL_SOCKET, SO_RCVBUF, reinterpret_cast<const char*>(&rcvbuf), sizeof(rcvbuf));

    sockaddr_in bind_addr = {};
    bind_addr.sin_family = AF_INET;
    bind_addr.sin_addr.s_addr = INADDR_ANY;
    bind_addr.sin_port = htons(static_cast<u_short>(m_listen_port));

    if (bind(m_socket, reinterpret_cast<const sockaddr*>(&bind_addr), sizeof(bind_addr)) != 0) {
        Cleanup();
        return false;
    }

    return true;
}

bool UdpReceiver::Start() {
    if (m_running.exchange(true)) {
        return true;
    }
    if (m_socket == INVALID_SOCKET) {
        m_running = false;
        return false;
    }
    m_thread = std::thread(&UdpReceiver::ReceiveLoop, this);
    return true;
}

void UdpReceiver::Stop() {
    m_running = false;
    if (m_socket != INVALID_SOCKET) {
        closesocket(m_socket);
        m_socket = INVALID_SOCKET;
    }
    if (m_thread.joinable()) {
        m_thread.join();
    }
}

void UdpReceiver::ReceiveLoop() {
    std::vector<uint8_t> recv_buffer(2048);
    std::vector<uint8_t> plaintext_buffer(STREAMER_MAX_PAYLOAD_SIZE);

    while (m_running) {
        sockaddr_in src_addr = {};
        int addr_len = sizeof(src_addr);
        int bytes = recvfrom(m_socket, reinterpret_cast<char*>(recv_buffer.data()),
                             static_cast<int>(recv_buffer.size()), 0,
                             reinterpret_cast<sockaddr*>(&src_addr), &addr_len);

        if (bytes <= 0 || !m_running) {
            break;
        }

        if (bytes < static_cast<int>(sizeof(PacketHeader) + STREAMER_GCM_TAG_LEN)) {
            continue; // Too small to be valid packet
        }

        PacketHeader header = {};
        memcpy(&header, recv_buffer.data(), sizeof(PacketHeader));

        if (header.magic != STREAMER_PACKET_MAGIC || header.version != STREAMER_PROTOCOL_VERSION) {
            continue;
        }

        const size_t expected_packet_size = sizeof(PacketHeader) + header.payload_len + STREAMER_GCM_TAG_LEN;
        if (static_cast<size_t>(bytes) != expected_packet_size) {
            continue;
        }

        const uint8_t* ciphertext = recv_buffer.data() + sizeof(PacketHeader);
        const uint8_t* auth_tag = ciphertext + header.payload_len;

        if (plaintext_buffer.size() < header.payload_len) {
            plaintext_buffer.resize(header.payload_len);
        }

        // Decrypt and authenticate
        if (!m_crypto.Decrypt(ciphertext, header.payload_len,
                              recv_buffer.data(), sizeof(PacketHeader), // AAD is header
                              header.iv, sizeof(header.iv),
                              auth_tag, STREAMER_GCM_TAG_LEN,
                              plaintext_buffer.data())) {
            // Authentication failed - corrupted or unauthorized packet, discard
            continue;
        }

        // Replay protection: 64-packet sliding window bitmask
        if (header.sequence_number > m_max_sequence) {
            uint32_t diff = header.sequence_number - m_max_sequence;
            if (diff < 64) {
                m_replay_window <<= diff;
            } else {
                m_replay_window = 0;
            }
            m_replay_window |= 1ULL;
            m_max_sequence = header.sequence_number;
        } else {
            uint32_t diff = m_max_sequence - header.sequence_number;
            if (diff >= 64 || (m_replay_window & (1ULL << diff)) != 0) {
                // Duplicate or replayed packet - discard
                continue;
            }
            m_replay_window |= (1ULL << diff);
        }

        // Ingest validated fragment into reassembler
        m_assembler.IngestFragment(header, plaintext_buffer.data(), header.payload_len);
    }
}

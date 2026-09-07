#ifndef CONDUIT_STREAMER_PROTOCOL_H
#define CONDUIT_STREAMER_PROTOCOL_H

#include <stdint.h>

#pragma pack(push, 1)

#define STREAMER_PACKET_MAGIC 0x4353 // "CS" (Conduit Stream)
#define STREAMER_PROTOCOL_VERSION 1

// Flag bits
#define STREAMER_FLAG_KEYFRAME       0x01
#define STREAMER_FLAG_START_OF_FRAME 0x02
#define STREAMER_FLAG_END_OF_FRAME   0x04

#define STREAMER_MAX_PAYLOAD_SIZE 1280 // Keeps datagram comfortably below 1500 byte MTU
#define STREAMER_GCM_IV_LEN       12
#define STREAMER_GCM_TAG_LEN      16

struct PacketHeader {
    uint16_t magic;           // STREAMER_PACKET_MAGIC (0x4353)
    uint8_t  version;         // STREAMER_PROTOCOL_VERSION (1)
    uint8_t  flags;           // STREAMER_FLAG_*
    uint32_t sequence_number; // Global packet sequence number
    uint32_t frame_id;        // Monotonic frame counter
    uint16_t fragment_index;  // 0-based fragment within frame
    uint16_t total_fragments; // Total number of fragments in this frame
    uint32_t timestamp_ms;    // Sender timestamp in milliseconds
    uint16_t payload_len;     // Encrypted payload byte length
    uint8_t  iv[STREAMER_GCM_IV_LEN]; // 12-byte AES-GCM Nonce
};

#pragma pack(pop)

#endif // CONDUIT_STREAMER_PROTOCOL_H

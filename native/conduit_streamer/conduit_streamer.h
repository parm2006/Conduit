#ifndef CONDUIT_STREAMER_H
#define CONDUIT_STREAMER_H

#ifdef _WIN32
  #ifdef CONDUIT_STREAMER_EXPORTS
    #define STREAMER_API __declspec(dllexport)
  #else
    #define STREAMER_API __declspec(dllimport)
  #endif
#else
  #define STREAMER_API
#endif

#include <winsock2.h>
#include <windows.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Event codes for Python notification
#define STREAMER_EVENT_STARTED        1
#define STREAMER_EVENT_STOPPED        2
#define STREAMER_EVENT_FIRST_FRAME    3
#define STREAMER_EVENT_NEED_KEYFRAME  4
#define STREAMER_EVENT_ERROR         -1

typedef void* StreamerSenderHandle;
typedef void* StreamerReceiverHandle;

typedef void (*StreamerEventCallback)(int event_code, const char* message, void* user_data);

// Sender (Client) API
STREAMER_API StreamerSenderHandle streamer_sender_create(StreamerEventCallback cb, void* user_data);
STREAMER_API int streamer_sender_start(StreamerSenderHandle handle, int display_index, const char* target_ip, int target_port, const unsigned char* session_key, int key_len, int bitrate_kbps, int fps);
STREAMER_API int streamer_sender_request_keyframe(StreamerSenderHandle handle);
STREAMER_API int streamer_sender_stop(StreamerSenderHandle handle);
STREAMER_API void streamer_sender_destroy(StreamerSenderHandle handle);

// Receiver (Server) API
STREAMER_API StreamerReceiverHandle streamer_receiver_create(HWND target_hwnd, StreamerEventCallback cb, void* user_data);
STREAMER_API int streamer_receiver_start(StreamerReceiverHandle handle, int listen_port, const unsigned char* session_key, int key_len);
STREAMER_API int streamer_receiver_resize(StreamerReceiverHandle handle, int width, int height);
STREAMER_API uint64_t streamer_receiver_get_frame_count(StreamerReceiverHandle handle);
STREAMER_API int streamer_receiver_stop(StreamerReceiverHandle handle);
STREAMER_API void streamer_receiver_destroy(StreamerReceiverHandle handle);

// Sender (Client) Queries
STREAMER_API uint64_t streamer_sender_get_frame_count(StreamerSenderHandle handle);

// Diagnostic & Versioning
STREAMER_API int streamer_get_version(void);
STREAMER_API int streamer_check_hardware_support(int* out_dxgi, int* out_encoder, int* out_decoder);
STREAMER_API int streamer_diagnose_adapters(char* buffer, int buffer_size);
STREAMER_API int streamer_test_capture_and_encode(int display_index, uint32_t* out_bytes, double* out_capture_ms, double* out_encode_ms);

#ifdef __cplusplus
}
#endif

#endif // CONDUIT_STREAMER_H

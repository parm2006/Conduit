#include "conduit_streamer.h"
#include "dxgi_capture.h"
#include "h264_encoder.h"
#include "h264_decoder.h"
#include "d3d11_renderer.h"
#include "udp_transport.h"
#include <windows.h>
#include <string>
#include <thread>
#include <atomic>
#include <chrono>

BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpvReserved) {
    (void)hinstDLL;
    (void)lpvReserved;
    switch (fdwReason) {
        case DLL_PROCESS_ATTACH: {
            DisableThreadLibraryCalls(hinstDLL);
            WSADATA wsaData;
            WSAStartup(MAKEWORD(2, 2), &wsaData);
            break;
        }
        case DLL_PROCESS_DETACH:
            WSACleanup();
            break;
    }
    return TRUE;
}

STREAMER_API int streamer_get_version(void) {
    return 100; // 1.0.0
}

STREAMER_API int streamer_check_hardware_support(int* out_dxgi, int* out_encoder, int* out_decoder) {
    if (out_dxgi) *out_dxgi = 0;
    if (out_encoder) *out_encoder = 0;
    if (out_decoder) *out_decoder = 0;

    HRESULT hr = MFStartup(MF_VERSION, MFSTARTUP_NOSOCKET);
    if (SUCCEEDED(hr)) {
        if (out_encoder) *out_encoder = 1;
        if (out_decoder) *out_decoder = 1;
        MFShutdown();
    }

    D3D_FEATURE_LEVEL featureLevel;
    ComPtr<ID3D11Device> pDevice;
    ComPtr<ID3D11DeviceContext> pContext;
    hr = D3D11CreateDevice(
        nullptr,
        D3D_DRIVER_TYPE_HARDWARE,
        nullptr,
        0,
        nullptr,
        0,
        D3D11_SDK_VERSION,
        &pDevice,
        &featureLevel,
        &pContext
    );
    if (SUCCEEDED(hr) && pDevice) {
        if (out_dxgi) *out_dxgi = 1;
    }
    return 0;
}

STREAMER_API int streamer_diagnose_adapters(char* buffer, int buffer_size) {
    if (!buffer || buffer_size <= 0) return -1;
    buffer[0] = '\0';

    ComPtr<IDXGIFactory1> factory;
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), &factory);
    if (FAILED(hr)) return -1;

    std::string report;
    ComPtr<IDXGIAdapter1> adapter;
    for (UINT a = 0; factory->EnumAdapters1(a, &adapter) != DXGI_ERROR_NOT_FOUND; ++a) {
        DXGI_ADAPTER_DESC1 desc;
        adapter->GetDesc1(&desc);

        char name[256];
        WideCharToMultiByte(CP_UTF8, 0, desc.Description, -1, name, sizeof(name), nullptr, nullptr);

        char line[512];
        snprintf(line, sizeof(line), "Adapter %u: %s (Flags=0x%X, DedicatedVRAM=%zu MB)\n",
                 a, name, desc.Flags, desc.DedicatedVideoMemory / (1024 * 1024));
        report += line;

        ComPtr<ID3D11Device> dev;
        ComPtr<ID3D11DeviceContext> ctx;
        D3D_FEATURE_LEVEL fl;
        HRESULT dhr = D3D11CreateDevice(adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr,
                                        D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0,
                                        D3D11_SDK_VERSION, &dev, &fl, &ctx);

        ComPtr<IDXGIOutput> output;
        for (UINT o = 0; adapter->EnumOutputs(o, &output) != DXGI_ERROR_NOT_FOUND; ++o) {
            DXGI_OUTPUT_DESC odesc;
            output->GetDesc(&odesc);
            char oname[256];
            WideCharToMultiByte(CP_UTF8, 0, odesc.DeviceName, -1, oname, sizeof(oname), nullptr, nullptr);

            ComPtr<IDXGIOutput1> out1;
            output.As(&out1);

            HRESULT duphr = E_FAIL;
            if (SUCCEEDED(dhr) && out1) {
                ComPtr<IDXGIOutputDuplication> dup;
                duphr = out1->DuplicateOutput(dev.Get(), &dup);
            }

            snprintf(line, sizeof(line), "  Output %u: %s [%ld,%ld - %ld,%ld] Attached=%d DupResult=0x%08X\n",
                     o, oname,
                     odesc.DesktopCoordinates.left, odesc.DesktopCoordinates.top,
                     odesc.DesktopCoordinates.right, odesc.DesktopCoordinates.bottom,
                     odesc.AttachedToDesktop, duphr);
            report += line;
        }
    }

    strncpy_s(buffer, buffer_size, report.c_str(), _TRUNCATE);
    return 0;
}

// ----------------------------------------------------------------------------
// StreamerSender Implementation
// ----------------------------------------------------------------------------
struct StreamerSenderState {
    StreamerEventCallback callback;
    void* user_data;
    DxgiCapture capture;
    H264Encoder encoder;
    UdpSender udp;
    std::thread worker_thread;
    std::atomic<bool> running;
    std::atomic<bool> force_keyframe;
    int display_index;
    int bitrate_kbps;
    int fps;

    StreamerSenderState(StreamerEventCallback cb, void* ud)
        : callback(cb)
        , user_data(ud)
        , running(false)
        , force_keyframe(true)
        , display_index(0)
        , bitrate_kbps(10000)
        , fps(60)
    {
    }

    void Notify(int event_code, const char* msg) {
        if (callback) {
            callback(event_code, msg, user_data);
        }
    }
};

static void StreamerSenderLoop(StreamerSenderState* state) {
    HRESULT hr = MFStartup(MF_VERSION, MFSTARTUP_NOSOCKET);
    if (FAILED(hr)) {
        state->Notify(STREAMER_EVENT_ERROR, "MFStartup failed on sender thread");
        state->running = false;
        return;
    }

    int init_rc = state->capture.Initialize(state->display_index);
    if (init_rc != 0) {
        char err[128];
        snprintf(err, sizeof(err), "DxgiCapture initialize failed with code 0x%08X", (unsigned int)init_rc);
        state->Notify(STREAMER_EVENT_ERROR, err);
        MFShutdown();
        state->running = false;
        return;
    }

    if (!state->encoder.Initialize(state->capture.GetDevice(),
                                  state->capture.GetWidth(), state->capture.GetHeight(),
                                  state->capture.GetWidth(), state->capture.GetHeight(),
                                  state->fps, state->bitrate_kbps)) {
        state->Notify(STREAMER_EVENT_ERROR, "H264Encoder initialize failed");
        state->capture.Cleanup();
        MFShutdown();
        state->running = false;
        return;
    }

    state->Notify(STREAMER_EVENT_STARTED, "Sender started");

    const auto frame_interval = std::chrono::microseconds(1000000 / state->fps);
    uint32_t frame_id = 1;
    CapturedFrame frame = {};
    std::vector<EncodedPacket> packets;

    while (state->running) {
        auto start_time = std::chrono::steady_clock::now();

        if (state->force_keyframe.exchange(false)) {
            state->encoder.RequestKeyframe();
        }

        if (state->capture.AcquireFrame(&frame, 16) && frame.texture) {
            packets.clear();
            if (state->encoder.EncodeFrame(frame.texture.Get(), frame_id++, &packets)) {
                for (const auto& packet : packets) {
                    state->udp.SendFrame(packet);
                }
            }
        }

        auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - start_time);
        if (elapsed < frame_interval) {
            std::this_thread::sleep_for(frame_interval - elapsed);
        }
    }

    state->encoder.Cleanup();
    state->capture.Cleanup();
    state->udp.Cleanup();
    MFShutdown();
    state->Notify(STREAMER_EVENT_STOPPED, "Sender stopped");
}

STREAMER_API StreamerSenderHandle streamer_sender_create(StreamerEventCallback cb, void* user_data) {
    return new StreamerSenderState(cb, user_data);
}

STREAMER_API int streamer_sender_start(StreamerSenderHandle handle, int display_index, const char* target_ip, int target_port, const unsigned char* session_key, int key_len, int bitrate_kbps, int fps) {
    if (!handle || !target_ip || target_port <= 0 || !session_key || key_len != 32) return -1;
    auto* state = static_cast<StreamerSenderState*>(handle);
    if (state->running) return 0;

    state->display_index = display_index;
    state->bitrate_kbps = (bitrate_kbps > 0) ? bitrate_kbps : 10000;
    state->fps = (fps > 0) ? fps : 60;
    state->force_keyframe = true;

    if (!state->udp.Initialize(target_ip, target_port, session_key, key_len)) {
        return -2;
    }

    state->running = true;
    state->worker_thread = std::thread(StreamerSenderLoop, state);
    return 0;
}

STREAMER_API int streamer_sender_request_keyframe(StreamerSenderHandle handle) {
    if (!handle) return -1;
    auto* state = static_cast<StreamerSenderState*>(handle);
    state->force_keyframe = true;
    return 0;
}

STREAMER_API int streamer_sender_stop(StreamerSenderHandle handle) {
    if (!handle) return -1;
    auto* state = static_cast<StreamerSenderState*>(handle);
    state->running = false;
    if (state->worker_thread.joinable()) {
        state->worker_thread.join();
    }
    return 0;
}

STREAMER_API void streamer_sender_destroy(StreamerSenderHandle handle) {
    if (!handle) return;
    auto* state = static_cast<StreamerSenderState*>(handle);
    streamer_sender_stop(handle);
    delete state;
}

// ----------------------------------------------------------------------------
// StreamerReceiver Implementation
// ----------------------------------------------------------------------------
struct StreamerReceiverState {
    HWND target_hwnd;
    StreamerEventCallback callback;
    void* user_data;
    D3D11Renderer renderer;
    H264Decoder decoder;
    UdpReceiver udp;
    std::atomic<bool> running;
    std::atomic<bool> first_frame_rendered;
    std::mutex render_mutex;

    StreamerReceiverState(HWND hwnd, StreamerEventCallback cb, void* ud)
        : target_hwnd(hwnd)
        , callback(cb)
        , user_data(ud)
        , running(false)
        , first_frame_rendered(false)
    {
    }

    void Notify(int event_code, const char* msg) {
        if (callback) {
            callback(event_code, msg, user_data);
        }
    }
};

STREAMER_API StreamerReceiverHandle streamer_receiver_create(HWND target_hwnd, StreamerEventCallback cb, void* user_data) {
    if (!target_hwnd) return nullptr;
    return new StreamerReceiverState(target_hwnd, cb, user_data);
}

STREAMER_API int streamer_receiver_start(StreamerReceiverHandle handle, int listen_port, const unsigned char* session_key, int key_len) {
    if (!handle || listen_port <= 0 || !session_key || key_len != 32) return -1;
    auto* state = static_cast<StreamerReceiverState*>(handle);
    if (state->running) return 0;

    HRESULT hr = MFStartup(MF_VERSION, MFSTARTUP_NOSOCKET);
    if (FAILED(hr)) return -2;

    RECT rc = {};
    GetClientRect(state->target_hwnd, &rc);
    uint32_t width = max(1, rc.right - rc.left);
    uint32_t height = max(1, rc.bottom - rc.top);

    if (!state->renderer.Initialize(state->target_hwnd, width, height)) {
        return -3;
    }

    if (!state->decoder.Initialize(state->renderer.GetDevice(), width, height)) {
        state->renderer.Cleanup();
        return -4;
    }

    state->first_frame_rendered = false;

    // Frame arrival callback from reassembler
    auto on_frame = [state](AssembledFrame&& frame) {
        std::lock_guard<std::mutex> lock(state->render_mutex);
        if (!state->running) return;

        ComPtr<ID3D11Texture2D> decodedTexture;
        uint32_t src_w = 0, src_h = 0;
        if (state->decoder.DecodeFrame(frame.data.data(), frame.data.size(), &decodedTexture, &src_w, &src_h)) {
            if (decodedTexture) {
                state->renderer.RenderFrame(decodedTexture.Get(), src_w, src_h);
                if (!state->first_frame_rendered.exchange(true)) {
                    state->Notify(STREAMER_EVENT_FIRST_FRAME, "First frame presented");
                }
            }
        }
    };

    // Keyframe needed callback
    auto on_keyframe_needed = [state]() {
        state->Notify(STREAMER_EVENT_NEED_KEYFRAME, "Keyframe required");
    };

    if (!state->udp.Initialize(listen_port, session_key, key_len, on_frame, on_keyframe_needed)) {
        state->decoder.Cleanup();
        state->renderer.Cleanup();
        return -5;
    }

    if (!state->udp.Start()) {
        state->udp.Cleanup();
        state->decoder.Cleanup();
        state->renderer.Cleanup();
        return -6;
    }

    state->running = true;
    state->Notify(STREAMER_EVENT_STARTED, "Receiver listening");
    return 0;
}

STREAMER_API int streamer_receiver_resize(StreamerReceiverHandle handle, int width, int height) {
    if (!handle) return -1;
    auto* state = static_cast<StreamerReceiverState*>(handle);
    std::lock_guard<std::mutex> lock(state->render_mutex);
    return state->renderer.Resize(width, height) ? 0 : -1;
}

STREAMER_API int streamer_receiver_stop(StreamerReceiverHandle handle) {
    if (!handle) return -1;
    auto* state = static_cast<StreamerReceiverState*>(handle);
    if (!state->running.exchange(false)) return 0;

    state->udp.Stop();
    state->udp.Cleanup();
    {
        std::lock_guard<std::mutex> lock(state->render_mutex);
        state->decoder.Cleanup();
        state->renderer.Cleanup();
    }
    MFShutdown();
    state->Notify(STREAMER_EVENT_STOPPED, "Receiver stopped");
    return 0;
}

STREAMER_API void streamer_receiver_destroy(StreamerReceiverHandle handle) {
    if (!handle) return;
    auto* state = static_cast<StreamerReceiverState*>(handle);
    streamer_receiver_stop(handle);
    delete state;
}

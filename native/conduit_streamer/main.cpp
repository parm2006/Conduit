#ifndef NOMINMAX
#define NOMINMAX
#endif
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
#include <memory>
#include <map>
#include <utility>
#include <stdexcept>
#include <iterator>
#include <mferror.h>
#include "receive_pipeline.h"
#include "send_scheduler.h"

// The experimental algorithms are explicitly opt-in; the normal path stays available.
static bool Stage1Enabled(const wchar_t* part) {
    wchar_t mode[32] = {};
    GetEnvironmentVariableW(L"CONDUIT_VIDEO_STAGE1", mode, 32);
    return wcscmp(mode, L"full") == 0 || wcscmp(mode, part) == 0;
}
static int CopyStats(const std::string& report, char* buffer, int size) {
    if (!buffer || size <= 0) return 0;
    const size_t count = (std::min)(report.size(), static_cast<size_t>(size - 1));
    memcpy(buffer, report.data(), count);
    buffer[count] = 0;
    return static_cast<int>(count);
}

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
    std::atomic<uint64_t> frame_count;
    int display_index;
    int bitrate_kbps;
    int fps;
    bool stage1_sender = false;
    conduit::StageStats capture_time, encode_time, send_time;
    std::atomic<uint64_t> send_failures{0};
    std::atomic<uint64_t> encoder_errors{0};
    std::atomic<uint32_t> last_encoder_error{0};
    uint64_t last_encoder_warning_us = 0;

    StreamerSenderState(StreamerEventCallback cb, void* ud)
        : callback(cb)
        , user_data(ud)
        , running(false)
        , force_keyframe(true)
        , frame_count(0)
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
    const HRESULT com_hr = state->stage1_sender ? CoInitializeEx(nullptr, COINIT_MULTITHREADED) : S_FALSE;
    if (FAILED(com_hr)) {
        state->Notify(STREAMER_EVENT_ERROR, "Sender COM initialization failed");
        state->running = false; return;
    }
    struct ComCleanup { bool active; ~ComCleanup() { if (active) CoUninitialize(); } } com_cleanup{state->stage1_sender};
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
    conduit::SendScheduler scheduler(static_cast<uint32_t>(state->fps));
    bool have_texture = false;
    uint32_t frame_id = 1;
    CapturedFrame frame = {};
    std::vector<EncodedPacket> packets;

    while (state->running) {
        auto start_time = std::chrono::steady_clock::now();
        if (state->force_keyframe.exchange(false)) {
            state->encoder.RequestKeyframe();
            scheduler.MarkDirty();
        }
        const uint64_t acquired_at = conduit::QpcNowUs();
        const uint32_t timeout = state->stage1_sender ? scheduler.WaitMs(acquired_at) : 16;
        const bool captured = state->capture.AcquireFrame(&frame, timeout) && frame.texture;
        state->capture_time.Add(conduit::QpcNowUs() - acquired_at);
        if (captured) {
            if (!have_texture) scheduler.MarkDirty();
            have_texture = true;
            scheduler.OnCapture(frame.desktop_changed);
        }
        const bool encode = state->stage1_sender
            ? have_texture && scheduler.Poll(conduit::QpcNowUs()) != conduit::SendScheduler::Action::Wait
            : captured;
        if (encode) {
            packets.clear();
            const uint64_t enc_at = conduit::QpcNowUs();
            const uint32_t input_id = state->stage1_sender ? frame_id : frame_id++;
            const bool output = state->encoder.EncodeFrame(frame.texture.Get(), input_id, &packets, frame.cursor_in_frame);
            state->encode_time.Add(conduit::QpcNowUs() - enc_at);
            const HRESULT encoder_error = state->encoder.GetLastError();
            if (FAILED(encoder_error)) {
                ++state->encoder_errors;
                state->last_encoder_error = static_cast<uint32_t>(encoder_error);
                const uint64_t now = conduit::QpcNowUs();
                if (!state->last_encoder_warning_us || now - state->last_encoder_warning_us >= 1000000) {
                    char message[128];
                    std::snprintf(message, sizeof message, "Encoder HRESULT=0x%08X; keyframe recovery requested",
                                  static_cast<unsigned>(encoder_error));
                    state->Notify(STREAMER_EVENT_WARNING, message);
                    state->last_encoder_warning_us = now;
                }
            }
            if (output && !packets.empty()) {
                const uint64_t send_at = conduit::QpcNowUs();
                for (auto& packet : packets) {
                    // IDs describe emitted access units, not calls that produced no output.
                    if (state->stage1_sender) packet.frame_id = frame_id++;
                    if (!state->udp.SendFrame(packet)) {
                        ++state->send_failures;
                        if (state->stage1_sender) state->force_keyframe = true;
                    }
                }
                state->send_time.Add(conduit::QpcNowUs() - send_at);
                state->frame_count.fetch_add(1);
                scheduler.OnEncoded(enc_at);
            } else if (state->stage1_sender) {
                // Keep the dirty state; do not busy-spin on encoder/device failure.
                scheduler.MarkDirty();
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
            }
        }
        if (!state->stage1_sender) {
            auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - start_time);
            if (elapsed < frame_interval) std::this_thread::sleep_for(frame_interval - elapsed);
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

    if (state->worker_thread.joinable()) state->worker_thread.join();
    state->stage1_sender = Stage1Enabled(L"sender");
    state->display_index = display_index;
    state->bitrate_kbps = (bitrate_kbps > 0) ? bitrate_kbps : 10000;
    state->fps = (fps > 0) ? fps : 60;
    state->force_keyframe = true;

    if (!state->udp.Initialize(target_ip, target_port, session_key, key_len)) {
        return -2;
    }

    state->running = true;
    try { state->worker_thread = std::thread(StreamerSenderLoop, state); }
    catch (...) { state->running = false; state->udp.Cleanup(); return -3; }
    return 0;
}

STREAMER_API int streamer_sender_set_cursor_overlay(StreamerSenderHandle handle, HWND hwnd) {
    if (!handle) return -1;
    static_cast<StreamerSenderState*>(handle)->capture.SetCursorOverlay(hwnd);
    return 0;
}

STREAMER_API int streamer_sender_request_keyframe(StreamerSenderHandle handle) {
    if (!handle) return -1;
    auto* state = static_cast<StreamerSenderState*>(handle);
    state->force_keyframe = true;
    return 0;
}

STREAMER_API uint64_t streamer_sender_get_frame_count(StreamerSenderHandle handle) {
    if (!handle) return 0;
    auto* state = static_cast<StreamerSenderState*>(handle);
    return state->frame_count.load();
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
    std::atomic<uint64_t> frame_count;
    std::atomic<int> cursor_in_frame{-1};
    std::mutex render_mutex;
    bool stage1_receiver = false;
    bool worker_mf_started = false;
    std::atomic<bool> need_keyframe{false};
    std::atomic<uint64_t> pending_resize{0};
    std::unique_ptr<conduit::ReceivePipeline> pipeline;
    struct InputMetadata {
        uint64_t first_rx_us, complete_us;
        int cursor_in_frame;
    };
    std::map<LONGLONG, InputMetadata> input_timings;

    StreamerReceiverState(HWND hwnd, StreamerEventCallback cb, void* ud)
        : target_hwnd(hwnd)
        , callback(cb)
        , user_data(ud)
        , running(false)
        , first_frame_rendered(false)
        , frame_count(0)
    {
    }

    void Notify(int event_code, const char* msg) {
        if (callback) {
            callback(event_code, msg, user_data);
        }
    }
};

struct Stage1DecodedHolder {
    ComPtr<ID3D11Texture2D> texture;
    ComPtr<IMFSample> sample;
    uint32_t subresource = 0;
};
static bool StartReceiverPipeline(StreamerReceiverState* state) {
    conduit::PipelineCallbacks cb;
    cb.initialize = [state] {
        if (FAILED(MFStartup(MF_VERSION, MFSTARTUP_NOSOCKET))) return false;
        state->worker_mf_started = true;
        RECT rect = {};
        GetClientRect(state->target_hwnd, &rect);
        const uint32_t width = (std::max)(1L, rect.right - rect.left);
        const uint32_t height = (std::max)(1L, rect.bottom - rect.top);
        return state->renderer.Initialize(state->target_hwnd, width, height, true)
            && state->decoder.Initialize(state->renderer.GetDevice(), width, height, true);
    };
    cb.shutdown = [state] {
        state->input_timings.clear();
        state->decoder.Cleanup();
        state->renderer.Cleanup();
        if (state->worker_mf_started) { MFShutdown(); state->worker_mf_started = false; }
    };
    cb.tick = [state] {
        const uint64_t packed = state->pending_resize.exchange(0);
        if (packed) {
            if (!state->renderer.Resize(static_cast<uint32_t>(packed >> 32), static_cast<uint32_t>(packed)))
                throw std::runtime_error("Renderer resize failed");
        }
        if (state->need_keyframe.exchange(false))
            state->Notify(STREAMER_EVENT_NEED_KEYFRAME, "Stage1 recovery requests IDR");
    };
    cb.decode = [state](const conduit::EncodedFrame& frame, conduit::DecodedFrame* out) {
        const LONGLONG input_time = state->decoder.GetNextSampleTime();
        state->input_timings[input_time] = {frame.first_rx_us, frame.complete_us, frame.cursor_in_frame};
        while (state->input_timings.size() > 64) state->input_timings.erase(state->input_timings.begin());
        auto holder = std::make_shared<Stage1DecodedHolder>();
        HRESULT status = E_FAIL;
        LONGLONG output_time = -1;
        const bool decoded = state->decoder.DecodeFrame(frame.data.data(), frame.data.size(),
            &holder->texture, &out->width, &out->height, &holder->sample,
            &holder->subresource, &output_time, &status);
        if (!decoded) {
            if (status == MF_E_TRANSFORM_NEED_MORE_INPUT) return conduit::DecodeStatus::NeedMoreInput;
            state->input_timings.clear();
            return conduit::DecodeStatus::Failed;
        }
        auto timing = state->input_timings.find(output_time);
        if (timing != state->input_timings.end()) {
            out->source_timing_known = true;
            out->first_rx_us = timing->second.first_rx_us;
            out->complete_us = timing->second.complete_us;
            out->cursor_in_frame = timing->second.cursor_in_frame;
            state->input_timings.erase(state->input_timings.begin(), std::next(timing));
        }
        out->handle = holder;
        return conduit::DecodeStatus::Ok;
    };
    cb.present = [state](const conduit::DecodedFrame& frame) {
        auto* holder = static_cast<Stage1DecodedHolder*>(frame.handle.get());
        const bool ok = holder && state->renderer.RenderFrame(holder->texture.Get(), frame.width, frame.height, holder->subresource);
        if (ok) {
            state->cursor_in_frame = frame.cursor_in_frame;
            ++state->frame_count;
        }
        return ok;
    };
    // Never invoke a Python/TLS callback on the UDP receive thread.
    cb.request_keyframe = [state] { state->need_keyframe = true; };
    cb.first_frame = [state] { state->first_frame_rendered = true; state->Notify(STREAMER_EVENT_FIRST_FRAME, "Stage1 first frame presented"); };
    cb.fatal = [state](const char* message) { state->Notify(STREAMER_EVENT_ERROR, message); };
    state->pipeline = std::make_unique<conduit::ReceivePipeline>(conduit::PipelineConfig{}, std::move(cb));
    return state->pipeline->Start();
}

STREAMER_API StreamerReceiverHandle streamer_receiver_create(HWND target_hwnd, StreamerEventCallback cb, void* user_data) {
    if (!target_hwnd) return nullptr;
    return new StreamerReceiverState(target_hwnd, cb, user_data);
}

STREAMER_API int streamer_receiver_start(StreamerReceiverHandle handle, int listen_port, const unsigned char* session_key, int key_len) {
    if (!handle || listen_port <= 0 || !session_key || key_len != 32) return -1;
    auto* state = static_cast<StreamerReceiverState*>(handle);
    if (state->running) return 0;

    state->stage1_receiver = Stage1Enabled(L"receiver");
    state->first_frame_rendered = false;
    state->cursor_in_frame = -1;
    state->input_timings.clear();
    state->need_keyframe = false;
    state->pending_resize = 0;
    if (state->pipeline) { state->pipeline->Stop(); state->pipeline.reset(); }
    if (state->stage1_receiver) {
        if (!StartReceiverPipeline(state)) { state->pipeline.reset(); return -3; }
    } else {
        HRESULT hr = MFStartup(MF_VERSION, MFSTARTUP_NOSOCKET);
        if (FAILED(hr)) return -2;
        RECT rc = {};
        GetClientRect(state->target_hwnd, &rc);
        uint32_t width = (std::max)(1L, rc.right - rc.left);
        uint32_t height = (std::max)(1L, rc.bottom - rc.top);
        if (!state->renderer.Initialize(state->target_hwnd, width, height)) { MFShutdown(); return -3; }
        if (!state->decoder.Initialize(state->renderer.GetDevice(), width, height,
                Stage1Enabled(L"decoder") || Stage1Enabled(L"sender"))) {
            state->renderer.Cleanup(); MFShutdown(); return -4;
        }
    }

    // Frame arrival callback from reassembler
    auto on_frame = [state](AssembledFrame&& frame) {
        if (!state->running) return;
        if (state->stage1_receiver && state->pipeline) {
            conduit::EncodedFrame encoded;
            encoded.data = std::move(frame.data);
            encoded.frame_id = frame.frame_id; encoded.keyframe = frame.is_keyframe;
            encoded.sender_ts_ms = frame.timestamp_ms;
            encoded.first_rx_us = frame.first_rx_us; encoded.complete_us = frame.complete_us;
            encoded.cursor_in_frame = frame.cursor_in_frame;
            state->pipeline->Submit(std::move(encoded));
            return;
        }
        std::lock_guard<std::mutex> lock(state->render_mutex);
        if (!state->running) return;

        ComPtr<ID3D11Texture2D> decodedTexture;
        ComPtr<IMFSample> sampleHolder;
        uint32_t src_w = 0, src_h = 0;
        uint32_t subresource = 0;
        LONGLONG output_time = -1;
        state->input_timings[state->decoder.GetNextSampleTime()] = {
            frame.first_rx_us, frame.complete_us, frame.cursor_in_frame};
        while (state->input_timings.size() > 64) state->input_timings.erase(state->input_timings.begin());
        if (state->decoder.DecodeFrame(frame.data.data(), frame.data.size(), &decodedTexture,
                &src_w, &src_h, &sampleHolder, &subresource, &output_time)) {
            if (decodedTexture) {
                if (!state->renderer.RenderFrame(decodedTexture.Get(), src_w, src_h, subresource)) return;
                const auto metadata = state->input_timings.find(output_time);
                state->cursor_in_frame = metadata != state->input_timings.end() ? metadata->second.cursor_in_frame : -1;
                if (metadata != state->input_timings.end())
                    state->input_timings.erase(state->input_timings.begin(), std::next(metadata));
                state->frame_count.fetch_add(1);
                if (!state->first_frame_rendered.exchange(true)) {
                    state->Notify(STREAMER_EVENT_FIRST_FRAME, "First frame presented");
                }
            }
        }
    };

    // Keyframe needed callback
    auto on_keyframe_needed = [state]() {
        if (state->stage1_receiver) state->need_keyframe = true;
        else state->Notify(STREAMER_EVENT_NEED_KEYFRAME, "Keyframe required");
    };

    auto cleanup = [state] {
        if (state->stage1_receiver) { if (state->pipeline) { state->pipeline->Stop(); state->pipeline.reset(); } }
        else { state->decoder.Cleanup(); state->renderer.Cleanup(); MFShutdown(); }
    };
    if (!state->udp.Initialize(listen_port, session_key, key_len, on_frame, on_keyframe_needed)) {
        cleanup(); return -5;
    }
    state->running = true;
    if (!state->udp.Start()) {
        state->running = false; state->udp.Cleanup(); cleanup(); return -6;
    }

    state->Notify(STREAMER_EVENT_STARTED, "Receiver listening");
    return 0;
}

STREAMER_API int streamer_receiver_resize(StreamerReceiverHandle handle, int width, int height) {
    if (!handle) return -1;
    auto* state = static_cast<StreamerReceiverState*>(handle);
    if (state->stage1_receiver) {
        if (width <= 0 || height <= 0) return -1;
        state->pending_resize = (static_cast<uint64_t>(width) << 32) | static_cast<uint32_t>(height);
        return 0;
    }
    std::lock_guard<std::mutex> lock(state->render_mutex);
    return state->renderer.Resize(width, height) ? 0 : -1;
}

STREAMER_API uint64_t streamer_receiver_get_frame_count(StreamerReceiverHandle handle) {
    if (!handle) return 0;
    auto* state = static_cast<StreamerReceiverState*>(handle);
    return state->frame_count.load();
}

STREAMER_API int streamer_receiver_get_cursor_in_frame(StreamerReceiverHandle handle) {
    if (!handle) return -1;
    return static_cast<StreamerReceiverState*>(handle)->cursor_in_frame.load();
}

STREAMER_API int streamer_receiver_stop(StreamerReceiverHandle handle) {
    if (!handle) return -1;
    auto* state = static_cast<StreamerReceiverState*>(handle);
    if (!state->running.exchange(false)) return 0;

    state->udp.Stop();
    state->udp.Cleanup();
    if (state->stage1_receiver) {
        if (state->pipeline) state->pipeline->Stop();
    } else {
        std::lock_guard<std::mutex> lock(state->render_mutex);
        state->decoder.Cleanup(); state->renderer.Cleanup(); MFShutdown();
    }
    state->Notify(STREAMER_EVENT_STOPPED, "Receiver stopped");
    return 0;
}

STREAMER_API void streamer_receiver_destroy(StreamerReceiverHandle handle) {
    if (!handle) return;
    auto* state = static_cast<StreamerReceiverState*>(handle);
    streamer_receiver_stop(handle);
    delete state;
}

STREAMER_API int streamer_sender_get_stats(StreamerSenderHandle handle, char* buffer, int size) {
    if (!handle) return 0;
    auto* state = static_cast<StreamerSenderState*>(handle);
    std::string report = state->stage1_sender ? "sender mode=stage1\n" : "sender mode=baseline\n";
    report += state->capture_time.Format("acquire call (includes waiting)");
    report += state->encode_time.Format("encode call");
    report += state->send_time.Format("send call");
    report += "send_failures=" + std::to_string(state->send_failures.load()) + "\n";
    char error[96];
    std::snprintf(error, sizeof error, "encoder_errors=%llu last_encoder_hresult=0x%08X\n",
                  static_cast<unsigned long long>(state->encoder_errors.load()), state->last_encoder_error.load());
    report += error;
    return CopyStats(report, buffer, size);
}
STREAMER_API int streamer_receiver_get_stats(StreamerReceiverHandle handle, char* buffer, int size) {
    if (!handle) return 0;
    auto* state = static_cast<StreamerReceiverState*>(handle);
    std::string report = state->pipeline ? state->pipeline->Report() : "receiver mode=baseline\n";
    report += "cursor_in_frame=" + std::to_string(state->cursor_in_frame.load()) + "\n";
    report += state->udp.GetStats();
    return CopyStats(report, buffer, size);
}

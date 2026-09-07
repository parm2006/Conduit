#ifndef CONDUIT_STREAMER_H264_ENCODER_H
#define CONDUIT_STREAMER_H264_ENCODER_H

#include <windows.h>
#include <d3d11.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mftransform.h>
#include <wrl/client.h>
#include <vector>
#include <stdint.h>

using Microsoft::WRL::ComPtr;

struct EncodedPacket {
    std::vector<uint8_t> data;
    bool is_keyframe;
    uint32_t frame_id;
    uint32_t timestamp_ms;
};

class H264Encoder {
public:
    H264Encoder();
    ~H264Encoder();

    bool Initialize(ID3D11Device* pDevice, uint32_t width, uint32_t height, uint32_t target_width, uint32_t target_height, uint32_t fps = 60, uint32_t bitrate_kbps = 10000);
    void Cleanup();

    // Request immediate IDR keyframe
    void RequestKeyframe();

    // Encode a captured D3D11 texture into H.264 NALUs
    bool EncodeFrame(ID3D11Texture2D* pTexture, uint32_t frame_id, std::vector<EncodedPacket>* out_packets);

    uint32_t GetOutputWidth() const { return m_target_width; }
    uint32_t GetOutputHeight() const { return m_target_height; }

private:
    bool SetupColorConverter();
    bool SetupEncoder(uint32_t fps, uint32_t bitrate_kbps);

    uint32_t m_width;
    uint32_t m_height;
    uint32_t m_target_width;
    uint32_t m_target_height;
    uint32_t m_fps;
    uint32_t m_bitrate_kbps;
    bool m_force_keyframe;
    LONGLONG m_frame_duration;
    LONGLONG m_sample_time;

    ComPtr<ID3D11Device> m_device;
    ComPtr<ID3D11DeviceContext> m_context;

    // D3D11 Video Processor for BGRA -> NV12 and Scaling
    ComPtr<ID3D11VideoDevice> m_video_device;
    ComPtr<ID3D11VideoContext> m_video_context;
    ComPtr<ID3D11VideoProcessorEnumerator> m_vp_enumerator;
    ComPtr<ID3D11VideoProcessor> m_vp;
    ComPtr<ID3D11Texture2D> m_nv12_texture;
    ComPtr<ID3D11VideoProcessorInputView> m_input_view;
    ComPtr<ID3D11VideoProcessorOutputView> m_output_view;

    // Media Foundation Transform (H.264 Encoder)
    ComPtr<IMFTransform> m_encoder;
    ComPtr<IMFDXGIDeviceManager> m_dxgi_manager;
    UINT m_dxgi_token;
    DWORD m_input_stream_id;
    DWORD m_output_stream_id;
};

#endif // CONDUIT_STREAMER_H264_ENCODER_H

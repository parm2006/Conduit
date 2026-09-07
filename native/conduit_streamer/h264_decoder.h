#ifndef CONDUIT_STREAMER_H264_DECODER_H
#define CONDUIT_STREAMER_H264_DECODER_H

#include <windows.h>
#include <d3d11.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mftransform.h>
#include <wrl/client.h>
#include <stdint.h>

using Microsoft::WRL::ComPtr;

class H264Decoder {
public:
    H264Decoder();
    ~H264Decoder();

    bool Initialize(ID3D11Device* pDevice, uint32_t width, uint32_t height);
    void Cleanup();

    // Feeds H.264 NALUs and extracts decoded D3D11 texture
    bool DecodeFrame(const uint8_t* data, size_t size, ComPtr<ID3D11Texture2D>* out_texture, uint32_t* out_width, uint32_t* out_height, ComPtr<IMFSample>* out_sample = nullptr);

private:
    HRESULT SetupDecoder();

    uint32_t m_width;
    uint32_t m_height;
    LONGLONG m_sample_time;
    UINT m_dxgi_token;

    ComPtr<ID3D11Device> m_device;
    ComPtr<ID3D11DeviceContext> m_context;
    ComPtr<IMFTransform> m_decoder;
    ComPtr<IMFDXGIDeviceManager> m_dxgi_manager;
};

#endif // CONDUIT_STREAMER_H264_DECODER_H

#ifndef CONDUIT_STREAMER_D3D11_RENDERER_H
#define CONDUIT_STREAMER_D3D11_RENDERER_H

#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <stdint.h>

using Microsoft::WRL::ComPtr;

class D3D11Renderer {
public:
    D3D11Renderer();
    ~D3D11Renderer();

    bool Initialize(HWND hwnd, uint32_t width, uint32_t height);
    void Cleanup();

    bool Resize(uint32_t width, uint32_t height);

    // Renders an NV12 or BGRA texture to the swapchain backbuffer with hardware scaling
    bool RenderFrame(ID3D11Texture2D* pTexture, uint32_t src_width, uint32_t src_height);

    ID3D11Device* GetDevice() const { return m_device.Get(); }

private:
    bool SetupVideoProcessor(uint32_t src_width, uint32_t src_height);

    HWND m_hwnd;
    uint32_t m_width;
    uint32_t m_height;
    uint32_t m_last_src_width;
    uint32_t m_last_src_height;

    ComPtr<ID3D11Device> m_device;
    ComPtr<ID3D11DeviceContext> m_context;
    ComPtr<IDXGISwapChain1> m_swap_chain;
    ComPtr<ID3D11Texture2D> m_back_buffer;
    ComPtr<ID3D11RenderTargetView> m_rtv;

    // Video processor for hardware color conversion and scaling
    ComPtr<ID3D11VideoDevice> m_video_device;
    ComPtr<ID3D11VideoContext> m_video_context;
    ComPtr<ID3D11VideoProcessorEnumerator> m_vp_enumerator;
    ComPtr<ID3D11VideoProcessor> m_vp;
    ComPtr<ID3D11VideoProcessorOutputView> m_output_view;
};

#endif // CONDUIT_STREAMER_D3D11_RENDERER_H

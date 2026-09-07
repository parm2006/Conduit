#include "d3d11_renderer.h"
#include <algorithm>
#include <cmath>

D3D11Renderer::D3D11Renderer()
    : m_hwnd(nullptr)
    , m_width(0)
    , m_height(0)
    , m_last_src_width(0)
    , m_last_src_height(0)
{
}

D3D11Renderer::~D3D11Renderer() {
    Cleanup();
}

void D3D11Renderer::Cleanup() {
    m_output_view.Reset();
    m_vp.Reset();
    m_vp_enumerator.Reset();
    m_video_context.Reset();
    m_video_device.Reset();
    m_rtv.Reset();
    m_back_buffer.Reset();
    m_swap_chain.Reset();
    m_context.Reset();
    m_device.Reset();
    m_hwnd = nullptr;
    m_width = 0;
    m_height = 0;
    m_last_src_width = 0;
    m_last_src_height = 0;
}

bool D3D11Renderer::Initialize(HWND hwnd, uint32_t width, uint32_t height) {
    Cleanup();
    if (!hwnd || width == 0 || height == 0) {
        return false;
    }

    m_hwnd = hwnd;
    m_width = width;
    m_height = height;

    D3D_FEATURE_LEVEL featureLevels[] = { D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0 };
    D3D_FEATURE_LEVEL featureLevel;
    HRESULT hr = D3D11CreateDevice(
        nullptr,
        D3D_DRIVER_TYPE_HARDWARE,
        nullptr,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        featureLevels,
        2,
        D3D11_SDK_VERSION,
        &m_device,
        &featureLevel,
        &m_context
    );
    if (FAILED(hr)) return false;

    // Create SwapChain
    ComPtr<IDXGIDevice> dxgiDevice;
    hr = m_device.As(&dxgiDevice);
    if (FAILED(hr)) return false;

    ComPtr<IDXGIAdapter> dxgiAdapter;
    hr = dxgiDevice->GetAdapter(&dxgiAdapter);
    if (FAILED(hr)) return false;

    ComPtr<IDXGIFactory2> dxgiFactory;
    hr = dxgiAdapter->GetParent(__uuidof(IDXGIFactory2), &dxgiFactory);
    if (FAILED(hr)) return false;

    DXGI_SWAP_CHAIN_DESC1 scDesc = {};
    scDesc.Width = m_width;
    scDesc.Height = m_height;
    scDesc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    scDesc.Stereo = FALSE;
    scDesc.SampleDesc.Count = 1;
    scDesc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    scDesc.BufferCount = 2; // Double buffering
    scDesc.Scaling = DXGI_SCALING_STRETCH;
    scDesc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    scDesc.AlphaMode = DXGI_ALPHA_MODE_IGNORE;

    hr = dxgiFactory->CreateSwapChainForHwnd(
        m_device.Get(),
        m_hwnd,
        &scDesc,
        nullptr,
        nullptr,
        &m_swap_chain
    );
    if (FAILED(hr)) return false;

    // Get backbuffer
    hr = m_swap_chain->GetBuffer(0, __uuidof(ID3D11Texture2D), &m_back_buffer);
    if (FAILED(hr)) return false;

    hr = m_device->CreateRenderTargetView(m_back_buffer.Get(), nullptr, &m_rtv);
    if (FAILED(hr)) return false;

    m_device.As(&m_video_device);
    m_context.As(&m_video_context);

    return true;
}

bool D3D11Renderer::Resize(uint32_t width, uint32_t height) {
    if (!m_swap_chain || width == 0 || height == 0) return false;
    m_rtv.Reset();
    m_back_buffer.Reset();
    m_output_view.Reset();
    m_vp.Reset();
    m_vp_enumerator.Reset();

    HRESULT hr = m_swap_chain->ResizeBuffers(2, width, height, DXGI_FORMAT_B8G8R8A8_UNORM, 0);
    if (FAILED(hr)) return false;

    m_width = width;
    m_height = height;

    hr = m_swap_chain->GetBuffer(0, __uuidof(ID3D11Texture2D), &m_back_buffer);
    if (FAILED(hr)) return false;

    hr = m_device->CreateRenderTargetView(m_back_buffer.Get(), nullptr, &m_rtv);
    if (FAILED(hr)) return false;

    m_last_src_width = 0;
    m_last_src_height = 0;
    return true;
}

bool D3D11Renderer::SetupVideoProcessor(uint32_t src_width, uint32_t src_height) {
    if (!m_video_device || !m_video_context) return false;
    m_output_view.Reset();
    m_vp.Reset();
    m_vp_enumerator.Reset();

    D3D11_VIDEO_PROCESSOR_CONTENT_DESC vpDesc = {};
    vpDesc.InputFrameFormat = D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE;
    vpDesc.InputFrameRate.Numerator = 60;
    vpDesc.InputFrameRate.Denominator = 1;
    vpDesc.InputWidth = src_width;
    vpDesc.InputHeight = src_height;
    vpDesc.OutputFrameRate.Numerator = 60;
    vpDesc.OutputFrameRate.Denominator = 1;
    vpDesc.OutputWidth = m_width;
    vpDesc.OutputHeight = m_height;
    vpDesc.Usage = D3D11_VIDEO_USAGE_PLAYBACK_NORMAL;

    HRESULT hr = m_video_device->CreateVideoProcessorEnumerator(&vpDesc, &m_vp_enumerator);
    if (FAILED(hr)) return false;

    hr = m_video_device->CreateVideoProcessor(m_vp_enumerator.Get(), 0, &m_vp);
    if (FAILED(hr)) return false;

    D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC outViewDesc = {};
    outViewDesc.ViewDimension = D3D11_VPOV_DIMENSION_TEXTURE2D;
    outViewDesc.Texture2D.MipSlice = 0;

    hr = m_video_device->CreateVideoProcessorOutputView(
        m_back_buffer.Get(),
        m_vp_enumerator.Get(),
        &outViewDesc,
        &m_output_view
    );
    if (FAILED(hr)) return false;

    m_last_src_width = src_width;
    m_last_src_height = src_height;
    return true;
}

bool D3D11Renderer::RenderFrame(ID3D11Texture2D* pTexture, uint32_t src_width, uint32_t src_height) {
    if (!pTexture || !m_swap_chain || !m_back_buffer) return false;

    if (m_last_src_width != src_width || m_last_src_height != src_height || !m_vp) {
        if (!SetupVideoProcessor(src_width, src_height)) {
            return false;
        }
    }

    D3D11_VIDEO_PROCESSOR_INPUT_VIEW_DESC inViewDesc = {};
    inViewDesc.ViewDimension = D3D11_VPIV_DIMENSION_TEXTURE2D;
    inViewDesc.Texture2D.MipSlice = 0;

    ComPtr<ID3D11VideoProcessorInputView> inputView;
    HRESULT hr = m_video_device->CreateVideoProcessorInputView(
        pTexture,
        m_vp_enumerator.Get(),
        &inViewDesc,
        &inputView
    );
    if (FAILED(hr)) return false;

    // Aspect-ratio preserving letterbox rect (pure black bars)
    float scale = (std::min)((float)m_width / src_width, (float)m_height / src_height);
    int fitted_w = (std::max)(1, (int)round(src_width * scale));
    int fitted_h = (std::max)(1, (int)round(src_height * scale));
    RECT destRect;
    destRect.left = (m_width - fitted_w) / 2;
    destRect.top = (m_height - fitted_h) / 2;
    destRect.right = destRect.left + fitted_w;
    destRect.bottom = destRect.top + fitted_h;

    RECT srcRect = { 0, 0, (LONG)src_width, (LONG)src_height };
    m_video_context->VideoProcessorSetStreamSourceRect(m_vp.Get(), 0, TRUE, &srcRect);
    m_video_context->VideoProcessorSetStreamDestRect(m_vp.Get(), 0, TRUE, &destRect);

    // Clear background to solid black
    const float black[4] = { 0.0f, 0.0f, 0.0f, 1.0f };
    m_context->ClearRenderTargetView(m_rtv.Get(), black);

    D3D11_VIDEO_PROCESSOR_STREAM stream = {};
    stream.Enable = TRUE;
    stream.pInputSurface = inputView.Get();

    hr = m_video_context->VideoProcessorBlt(
        m_vp.Get(),
        m_output_view.Get(),
        0,
        1,
        &stream
    );
    if (FAILED(hr)) return false;

    // Present with V-Sync (1) or immediate (0)
    hr = m_swap_chain->Present(1, 0);
    return SUCCEEDED(hr);
}

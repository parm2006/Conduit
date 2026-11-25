#include "dxgi_capture.h"
#include <stdio.h>

static conduit::CursorObservation ObserveCursor(HWND overlay, const RECT& output) {
    CURSORINFO cursor = {};
    cursor.cbSize = sizeof cursor;
    if (!GetCursorInfo(&cursor)) return {};
    const bool showing = (cursor.flags & CURSOR_SHOWING) && !(cursor.flags & CURSOR_SUPPRESSED);
    bool in_output = false;
    if (!showing && overlay && IsWindowVisible(overlay)) {
        RECT bounds = {}, intersection = {};
        if (!GetWindowRect(overlay, &bounds)) return {};
        in_output = IntersectRect(&intersection, &bounds, &output) != FALSE;
    }
    return {true, showing, in_output};
}

DxgiCapture::DxgiCapture()
    : m_output_index(0)
    , m_width(0)
    , m_height(0)
    , m_last_cursor_x(0)
    , m_last_cursor_y(0)
    , m_cursor_visible(false)
{
}

DxgiCapture::~DxgiCapture() {
    Cleanup();
}

void DxgiCapture::Cleanup() {
    m_cursor_policy.Reset();
    m_have_frame = false;
    m_duplication.Reset();
    m_output1.Reset();
    m_shared_texture.Reset();
    m_acquired_texture.Reset();
    m_context.Reset();
    m_device.Reset();
}

int DxgiCapture::Initialize(int output_index) {
    Cleanup();
    m_output_index = output_index;

    ComPtr<IDXGIFactory1> dxgiFactory;
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), &dxgiFactory);
    if (FAILED(hr)) return -101;

    // Search across adapters for the requested output index
    ComPtr<IDXGIAdapter1> adapter;
    ComPtr<IDXGIAdapter1> targetAdapter;
    ComPtr<IDXGIOutput> targetOutput;
    UINT currentOutputCount = 0;

    for (UINT adapterIdx = 0; dxgiFactory->EnumAdapters1(adapterIdx, &adapter) != DXGI_ERROR_NOT_FOUND; ++adapterIdx) {
        DXGI_ADAPTER_DESC1 adapterDesc;
        adapter->GetDesc1(&adapterDesc);

        ComPtr<IDXGIOutput> output;
        for (UINT outIdx = 0; adapter->EnumOutputs(outIdx, &output) != DXGI_ERROR_NOT_FOUND; ++outIdx) {
            if (static_cast<int>(currentOutputCount) == output_index) {
                targetAdapter = adapter;
                targetOutput = output;
                break;
            }
            currentOutputCount++;
        }
        if (targetOutput) break;
    }

    if (!targetAdapter) return -102;
    if (!targetOutput) return -103;

    // Create D3D11Device specifically on targetAdapter
    D3D_FEATURE_LEVEL featureLevels[] = { D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0 };
    D3D_FEATURE_LEVEL featureLevel;
    hr = D3D11CreateDevice(
        targetAdapter.Get(),
        D3D_DRIVER_TYPE_UNKNOWN, // Required when passing explicit adapter
        nullptr,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        featureLevels,
        2,
        D3D11_SDK_VERSION,
        &m_device,
        &featureLevel,
        &m_context
    );
    if (FAILED(hr)) {
        hr = D3D11CreateDevice(
            targetAdapter.Get(),
            D3D_DRIVER_TYPE_UNKNOWN,
            nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            nullptr,
            0,
            D3D11_SDK_VERSION,
            &m_device,
            &featureLevel,
            &m_context
        );
        if (FAILED(hr)) return -104;
    }

    hr = targetOutput.As(&m_output1);
    if (FAILED(hr)) return -105;

    DXGI_OUTPUT_DESC desc;
    targetOutput->GetDesc(&desc);
    m_output_rect = desc.DesktopCoordinates;
    m_width = desc.DesktopCoordinates.right - desc.DesktopCoordinates.left;
    m_height = desc.DesktopCoordinates.bottom - desc.DesktopCoordinates.top;

    HRESULT dup_hr = SetupDuplication();
    if (FAILED(dup_hr)) return (int)dup_hr;
    return 0;
}

HRESULT DxgiCapture::SetupDuplication() {
    m_cursor_policy.Reset();
    m_have_frame = false;
    m_duplication.Reset();
    if (!m_output1 || !m_device) return E_POINTER;

    // Attach calling thread to interactive input desktop
    HDESK hDesk = OpenInputDesktop(0, FALSE, GENERIC_ALL);
    if (!hDesk) {
        hDesk = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS | DESKTOP_WRITEOBJECTS | DESKTOP_SWITCHDESKTOP);
    }
    if (hDesk) {
        SetThreadDesktop(hDesk);
        CloseDesktop(hDesk);
    }

    HRESULT hr = m_output1->DuplicateOutput(m_device.Get(), &m_duplication);
    if (FAILED(hr)) {
        return hr;
    }

    // Create shared texture to hold the persistent desktop image
    D3D11_TEXTURE2D_DESC texDesc = {};
    texDesc.Width = m_width;
    texDesc.Height = m_height;
    texDesc.MipLevels = 1;
    texDesc.ArraySize = 1;
    texDesc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    texDesc.SampleDesc.Count = 1;
    texDesc.Usage = D3D11_USAGE_DEFAULT;
    texDesc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;

    hr = m_device->CreateTexture2D(&texDesc, nullptr, &m_shared_texture);
    return hr;
}

bool DxgiCapture::AcquireFrame(CapturedFrame* out_frame, uint32_t timeout_ms) {
    if (!out_frame) return false;
    out_frame->texture.Reset();
    out_frame->has_new_frame = false;
    out_frame->desktop_changed = false;
    out_frame->width = m_width;
    out_frame->height = m_height;
    out_frame->cursor_x = m_last_cursor_x;
    out_frame->cursor_y = m_last_cursor_y;
    out_frame->cursor_visible = m_cursor_visible;
    out_frame->cursor_in_frame = m_cursor_policy.Value();

    if (!m_duplication) {
        if (FAILED(SetupDuplication())) return false;
        out_frame->cursor_in_frame = m_cursor_policy.Value();
    }

    DXGI_OUTDUPL_FRAME_INFO frameInfo = {};
    ComPtr<IDXGIResource> desktopResource;

    const HWND cursor_overlay = m_cursor_overlay.load();
    const auto cursor_before = ObserveCursor(cursor_overlay, m_output_rect);
    HRESULT hr = m_duplication->AcquireNextFrame(timeout_ms, &frameInfo, &desktopResource);
    if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
        // No new frame within timeout, but existing shared texture is still valid
        if (m_have_frame && m_shared_texture) {
            out_frame->texture = m_shared_texture;
            return true;
        }
        return false;
    }

    if (hr == DXGI_ERROR_ACCESS_LOST) {
        SetupDuplication();
        return false;
    }

    if (FAILED(hr)) {
        return false;
    }

    // Cursor position update
    const auto cursor_after = ObserveCursor(cursor_overlay, m_output_rect);
    // Retain pointer ownership even if this image is dropped for a source race.
    // This does not change the policy paired with the last copied texture.
    m_cursor_policy.ObservePointer(
        frameInfo.LastMouseUpdateTime.QuadPart != 0, frameInfo.PointerPosition.Visible != FALSE);
    if (frameInfo.PointerPosition.Visible) {
        m_last_cursor_x = frameInfo.PointerPosition.Position.x;
        m_last_cursor_y = frameInfo.PointerPosition.Position.y;
        m_cursor_visible = true;
    } else if (frameInfo.LastMouseUpdateTime.QuadPart != 0) {
        m_cursor_visible = false;
    }
    out_frame->cursor_x = m_last_cursor_x;
    out_frame->cursor_y = m_last_cursor_y;
    out_frame->cursor_visible = m_cursor_visible;

    bool copied = false;
    if (desktopResource) {
        ComPtr<ID3D11Texture2D> acquiredTex;
        hr = desktopResource.As(&acquiredTex);
        if (SUCCEEDED(hr) && acquiredTex && m_shared_texture &&
            conduit::CursorObservationsAgree(cursor_before, cursor_after)) {
            m_context->CopyResource(m_shared_texture.Get(), acquiredTex.Get());
            const bool cursor_policy_changed = m_cursor_policy.Update(
                frameInfo.LastMouseUpdateTime.QuadPart != 0, frameInfo.PointerPosition.Visible != FALSE,
                cursor_before, cursor_after);
            out_frame->texture = m_shared_texture;
            out_frame->cursor_in_frame = m_cursor_policy.Value();
            out_frame->has_new_frame = true;
            out_frame->desktop_changed = frameInfo.LastPresentTime.QuadPart != 0 || cursor_policy_changed;
            m_have_frame = true;
            copied = true;
        }
        acquiredTex.Reset();
        desktopResource.Reset();
    }

    m_duplication->ReleaseFrame();

    if (!copied && m_have_frame && m_shared_texture) {
        out_frame->texture = m_shared_texture;
    }

    return (out_frame->texture != nullptr);
}

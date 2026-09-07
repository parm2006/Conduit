#ifndef CONDUIT_STREAMER_DXGI_CAPTURE_H
#define CONDUIT_STREAMER_DXGI_CAPTURE_H

#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <stdint.h>

using Microsoft::WRL::ComPtr;

struct CapturedFrame {
    ComPtr<ID3D11Texture2D> texture;
    uint32_t width;
    uint32_t height;
    int cursor_x;
    int cursor_y;
    bool cursor_visible;
    bool has_new_frame;
};

class DxgiCapture {
public:
    DxgiCapture();
    ~DxgiCapture();

    int Initialize(int output_index);
    void Cleanup();
    ID3D11Device* GetDevice() const { return m_device.Get(); }

    // Acquires next frame. If timeout occurs and timeout_ok is true, returns true with has_new_frame=false.
    bool AcquireFrame(CapturedFrame* out_frame, uint32_t timeout_ms = 16);

    uint32_t GetWidth() const { return m_width; }
    uint32_t GetHeight() const { return m_height; }

private:
    HRESULT SetupDuplication();

    int m_output_index;
    uint32_t m_width;
    uint32_t m_height;

    ComPtr<ID3D11Device> m_device;
    ComPtr<ID3D11DeviceContext> m_context;
    ComPtr<IDXGIOutput1> m_output1;
    ComPtr<IDXGIOutputDuplication> m_duplication;
    ComPtr<ID3D11Texture2D> m_acquired_texture;
    ComPtr<ID3D11Texture2D> m_shared_texture; // Copy of the latest complete frame

    int m_last_cursor_x;
    int m_last_cursor_y;
    bool m_cursor_visible;
};

#endif // CONDUIT_STREAMER_DXGI_CAPTURE_H

#include "h264_decoder.h"
#include <stdio.h>
#include <codecapi.h>
#include <mfapi.h>
#include <mferror.h>
#include <wmcodecdsp.h>

H264Decoder::H264Decoder()
    : m_width(0)
    , m_height(0)
    , m_sample_time(0)
    , m_dxgi_token(0)
{
}

H264Decoder::~H264Decoder() {
    Cleanup();
}

void H264Decoder::Cleanup() {
    m_decoder.Reset();
    m_dxgi_manager.Reset();
    m_context.Reset();
    m_device.Reset();
}

bool H264Decoder::Initialize(ID3D11Device* pDevice, uint32_t width, uint32_t height) {
    Cleanup();
    if (!pDevice || width == 0 || height == 0) return false;

    m_device = pDevice;
    m_device->GetImmediateContext(&m_context);
    m_width = width;
    m_height = height;
    m_sample_time = 0;

    HRESULT hr = SetupDecoder();
    if (FAILED(hr)) {
        printf("[DECODER] SetupDecoder failed: 0x%08X\n", hr); fflush(stdout);
        return false;
    }
    return true;
}

HRESULT H264Decoder::SetupDecoder() {
    HRESULT hr = CoCreateInstance(CLSID_CMSH264DecoderMFT, nullptr, CLSCTX_INPROC_SERVER,
                                 IID_IMFTransform, reinterpret_cast<void**>(m_decoder.GetAddressOf()));
    if (FAILED(hr)) return hr;

    // Enable D3D11 hardware acceleration on decoder
    hr = MFCreateDXGIDeviceManager(&m_dxgi_token, &m_dxgi_manager);
    if (SUCCEEDED(hr)) {
        hr = m_dxgi_manager->ResetDevice(m_device.Get(), m_dxgi_token);
        if (SUCCEEDED(hr)) {
            m_decoder->ProcessMessage(MFT_MESSAGE_SET_D3D_MANAGER, ULONG_PTR(m_dxgi_manager.Get()));
        }
    }

    // Configure low latency mode on decoder
    ComPtr<ICodecAPI> codecApi;
    if (SUCCEEDED(m_decoder.As(&codecApi))) {
        VARIANT var = {};
        var.vt = VT_BOOL;
        var.boolVal = VARIANT_TRUE;
        codecApi->SetValue(&CODECAPI_AVLowLatencyMode, &var);
    }

    // Set Input Media Type (H.264)
    ComPtr<IMFMediaType> inType;
    hr = MFCreateMediaType(&inType);
    if (FAILED(hr)) return hr;

    inType->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    inType->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_H264);
    MFSetAttributeSize(inType.Get(), MF_MT_FRAME_SIZE, m_width, m_height);

    hr = m_decoder->SetInputType(0, inType.Get(), 0);
    if (FAILED(hr)) return hr;

    // Query and select available output type (NV12)
    bool outputSet = false;
    for (DWORD i = 0; ; ++i) {
        ComPtr<IMFMediaType> availType;
        HRESULT availHr = m_decoder->GetOutputAvailableType(0, i, &availType);
        if (FAILED(availHr)) break;

        GUID subtype = {};
        availType->GetGUID(MF_MT_SUBTYPE, &subtype);
        if (subtype == MFVideoFormat_NV12) {
            hr = m_decoder->SetOutputType(0, availType.Get(), 0);
            if (SUCCEEDED(hr)) {
                outputSet = true;
                break;
            }
        }
    }

    if (!outputSet) {
        // Fallback: use first available output type
        ComPtr<IMFMediaType> firstType;
        if (SUCCEEDED(m_decoder->GetOutputAvailableType(0, 0, &firstType))) {
            hr = m_decoder->SetOutputType(0, firstType.Get(), 0);
            if (SUCCEEDED(hr)) outputSet = true;
        }
    }

    if (!outputSet) return MF_E_INVALIDMEDIATYPE;

    m_decoder->ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0);
    m_decoder->ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0);

    return S_OK;
}

bool H264Decoder::DecodeFrame(const uint8_t* data, size_t size, ComPtr<ID3D11Texture2D>* out_texture,
                              uint32_t* out_width, uint32_t* out_height) {
    if (!data || size == 0 || !m_decoder || !out_texture) return false;

    // 1. Create input sample with H.264 data
    ComPtr<IMFMediaBuffer> inBuffer;
    HRESULT hr = MFCreateMemoryBuffer(static_cast<DWORD>(size), &inBuffer);
    if (FAILED(hr)) return false;

    BYTE* pBuffer = nullptr;
    hr = inBuffer->Lock(&pBuffer, nullptr, nullptr);
    if (FAILED(hr) || !pBuffer) return false;

    memcpy(pBuffer, data, size);
    inBuffer->Unlock();
    inBuffer->SetCurrentLength(static_cast<DWORD>(size));

    ComPtr<IMFSample> inSample;
    hr = MFCreateSample(&inSample);
    if (FAILED(hr)) return false;

    hr = inSample->AddBuffer(inBuffer.Get());
    if (FAILED(hr)) return false;

    inSample->SetSampleTime(m_sample_time);
    inSample->SetSampleDuration(166666); // ~60fps
    m_sample_time += 166666;

    hr = m_decoder->ProcessInput(0, inSample.Get(), 0);
    if (FAILED(hr)) return false;

    // 2. Drain decoded output
    MFT_OUTPUT_DATA_BUFFER outputData = {};
    outputData.dwStreamID = 0;

    MFT_OUTPUT_STREAM_INFO streamInfo = {};
    m_decoder->GetOutputStreamInfo(0, &streamInfo);

    // If decoder does not provide its own samples, allocate one
    ComPtr<IMFSample> outSample;
    ComPtr<IMFMediaBuffer> outBuffer;
    if ((streamInfo.dwFlags & (MFT_OUTPUT_STREAM_PROVIDES_SAMPLES | MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES)) == 0) {
        hr = MFCreateSample(&outSample);
        if (SUCCEEDED(hr)) {
            hr = MFCreateMemoryBuffer(streamInfo.cbSize > 0 ? streamInfo.cbSize : m_width * m_height * 2, &outBuffer);
            if (SUCCEEDED(hr)) {
                outSample->AddBuffer(outBuffer.Get());
                outputData.pSample = outSample.Get();
            }
        }
    }

    DWORD status = 0;
    hr = m_decoder->ProcessOutput(0, 1, &outputData, &status);
    if (hr == MF_E_TRANSFORM_STREAM_CHANGE) {
        // Negotiate new output type if decoder detected SPS/PPS change
        ComPtr<IMFMediaType> newType;
        hr = m_decoder->GetOutputAvailableType(0, 0, &newType);
        if (SUCCEEDED(hr)) {
            m_decoder->SetOutputType(0, newType.Get(), 0);
            UINT32 w = 0, h = 0;
            MFGetAttributeSize(newType.Get(), MF_MT_FRAME_SIZE, &w, &h);
            if (w > 0 && h > 0) {
                m_width = w;
                m_height = h;
            }
        }
        // Retry process output
        hr = m_decoder->ProcessOutput(0, 1, &outputData, &status);
    }

    if (FAILED(hr) || !outputData.pSample) {
        return false;
    }

    // 3. Extract D3D11 texture from sample
    ComPtr<IMFMediaBuffer> mediaBuffer;
    hr = outputData.pSample->GetBufferByIndex(0, &mediaBuffer);
    if (FAILED(hr)) return false;

    ComPtr<IMFDXGIBuffer> dxgiBuffer;
    hr = mediaBuffer.As(&dxgiBuffer);
    if (SUCCEEDED(hr)) {
        ComPtr<ID3D11Texture2D> tex;
        hr = dxgiBuffer->GetResource(__uuidof(ID3D11Texture2D), reinterpret_cast<void**>(tex.GetAddressOf()));
        if (SUCCEEDED(hr) && tex) {
            *out_texture = tex;
            if (out_width) *out_width = m_width;
            if (out_height) *out_height = m_height;
            return true;
        }
    }

    return false;
}

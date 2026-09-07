#include "h264_encoder.h"
#include <codecapi.h>
#include <mfapi.h>
#include <mferror.h>
#include <wmcodecdsp.h>

#pragma comment(lib, "mfplat.lib")
#pragma comment(lib, "mfuuid.lib")

H264Encoder::H264Encoder()
    : m_width(0)
    , m_height(0)
    , m_target_width(0)
    , m_target_height(0)
    , m_fps(60)
    , m_bitrate_kbps(10000)
    , m_force_keyframe(true)
    , m_frame_duration(0)
    , m_sample_time(0)
    , m_dxgi_token(0)
    , m_input_stream_id(0)
    , m_output_stream_id(0)
{
}

H264Encoder::~H264Encoder() {
    Cleanup();
}

void H264Encoder::Cleanup() {
    m_encoder.Reset();
    m_dxgi_manager.Reset();
    m_output_view.Reset();
    m_input_view.Reset();
    m_nv12_texture.Reset();
    m_vp.Reset();
    m_vp_enumerator.Reset();
    m_video_context.Reset();
    m_video_device.Reset();
    m_context.Reset();
    m_device.Reset();
}

bool H264Encoder::Initialize(ID3D11Device* pDevice, uint32_t width, uint32_t height,
                             uint32_t target_width, uint32_t target_height,
                             uint32_t fps, uint32_t bitrate_kbps) {
    Cleanup();
    if (!pDevice || width == 0 || height == 0 || target_width == 0 || target_height == 0) {
        return false;
    }

    m_device = pDevice;
    m_device->GetImmediateContext(&m_context);
    m_width = width;
    m_height = height;
    m_target_width = (target_width + 1) & ~1;   // Must be even for NV12
    m_target_height = (target_height + 1) & ~1; // Must be even for NV12
    m_fps = (fps > 0) ? fps : 60;
    m_bitrate_kbps = (bitrate_kbps > 0) ? bitrate_kbps : 10000;
    m_frame_duration = 10000000LL / m_fps; // 100-ns units
    m_sample_time = 0;
    m_force_keyframe = true;

    if (!SetupColorConverter()) {
        return false;
    }

    if (!SetupEncoder(m_fps, m_bitrate_kbps)) {
        return false;
    }

    return true;
}

bool H264Encoder::SetupColorConverter() {
    HRESULT hr = m_device.As(&m_video_device);
    if (FAILED(hr)) return false;

    hr = m_context.As(&m_video_context);
    if (FAILED(hr)) return false;

    // Create NV12 target texture
    D3D11_TEXTURE2D_DESC nv12Desc = {};
    nv12Desc.Width = m_target_width;
    nv12Desc.Height = m_target_height;
    nv12Desc.MipLevels = 1;
    nv12Desc.ArraySize = 1;
    nv12Desc.Format = DXGI_FORMAT_NV12;
    nv12Desc.SampleDesc.Count = 1;
    nv12Desc.Usage = D3D11_USAGE_DEFAULT;
    nv12Desc.BindFlags = D3D11_BIND_RENDER_TARGET;

    hr = m_device->CreateTexture2D(&nv12Desc, nullptr, &m_nv12_texture);
    if (FAILED(hr)) return false;

    // Setup Video Processor Enumerator
    D3D11_VIDEO_PROCESSOR_CONTENT_DESC vpDesc = {};
    vpDesc.InputFrameFormat = D3D11_VIDEO_FRAME_FORMAT_PROGRESSIVE;
    vpDesc.InputFrameRate.Numerator = m_fps;
    vpDesc.InputFrameRate.Denominator = 1;
    vpDesc.InputWidth = m_width;
    vpDesc.InputHeight = m_height;
    vpDesc.OutputFrameRate.Numerator = m_fps;
    vpDesc.OutputFrameRate.Denominator = 1;
    vpDesc.OutputWidth = m_target_width;
    vpDesc.OutputHeight = m_target_height;
    vpDesc.Usage = D3D11_VIDEO_USAGE_PLAYBACK_NORMAL;

    hr = m_video_device->CreateVideoProcessorEnumerator(&vpDesc, &m_vp_enumerator);
    if (FAILED(hr)) return false;

    hr = m_video_device->CreateVideoProcessor(m_vp_enumerator.Get(), 0, &m_vp);
    if (FAILED(hr)) return false;

    // Setup Output View
    D3D11_VIDEO_PROCESSOR_OUTPUT_VIEW_DESC outViewDesc = {};
    outViewDesc.ViewDimension = D3D11_VPOV_DIMENSION_TEXTURE2D;
    outViewDesc.Texture2D.MipSlice = 0;

    hr = m_video_device->CreateVideoProcessorOutputView(
        m_nv12_texture.Get(),
        m_vp_enumerator.Get(),
        &outViewDesc,
        &m_output_view
    );
    return SUCCEEDED(hr);
}

bool H264Encoder::SetupEncoder(uint32_t fps, uint32_t bitrate_kbps) {
    HRESULT hr = CoCreateInstance(CLSID_CMSH264EncoderMFT, nullptr, CLSCTX_INPROC_SERVER,
                                 IID_IMFTransform, reinterpret_cast<void**>(m_encoder.GetAddressOf()));
    if (FAILED(hr)) return false;

    // Enable D3D11 hardware acceleration on the MFT
    hr = MFCreateDXGIDeviceManager(&m_dxgi_token, &m_dxgi_manager);
    if (SUCCEEDED(hr)) {
        hr = m_dxgi_manager->ResetDevice(m_device.Get(), m_dxgi_token);
        if (SUCCEEDED(hr)) {
            m_encoder->ProcessMessage(MFT_MESSAGE_SET_D3D_MANAGER, ULONG_PTR(m_dxgi_manager.Get()));
        }
    }

    // Configure low latency mode and rate control via ICodecAPI
    ComPtr<ICodecAPI> codecApi;
    hr = m_encoder.As(&codecApi);
    if (SUCCEEDED(hr)) {
        VARIANT var = {};
        var.vt = VT_BOOL;
        var.boolVal = VARIANT_TRUE;
        codecApi->SetValue(&CODECAPI_AVLowLatencyMode, &var);

        var.vt = VT_UI4;
        var.ulVal = eAVEncCommonRateControlMode_CBR;
        codecApi->SetValue(&CODECAPI_AVEncCommonRateControlMode, &var);

        var.vt = VT_UI4;
        var.ulVal = bitrate_kbps * 1000;
        codecApi->SetValue(&CODECAPI_AVEncCommonMeanBitRate, &var);

        var.vt = VT_UI4;
        var.ulVal = fps * 2; // Keyframe at least every 2 seconds
        codecApi->SetValue(&CODECAPI_AVEncMPVGOPSize, &var);

        var.vt = VT_UI4;
        var.ulVal = 0; // 0 B-frames for zero latency
        codecApi->SetValue(&CODECAPI_AVEncMPVDefaultBPictureCount, &var);
    }

    // Set Output Media Type (H.264)
    ComPtr<IMFMediaType> outType;
    hr = MFCreateMediaType(&outType);
    if (FAILED(hr)) return false;

    outType->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    outType->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_H264);
    outType->SetUINT32(MF_MT_AVG_BITRATE, bitrate_kbps * 1000);
    MFSetAttributeSize(outType.Get(), MF_MT_FRAME_SIZE, m_target_width, m_target_height);
    MFSetAttributeRatio(outType.Get(), MF_MT_FRAME_RATE, fps, 1);
    MFSetAttributeRatio(outType.Get(), MF_MT_PIXEL_ASPECT_RATIO, 1, 1);
    outType->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);
    outType->SetUINT32(MF_MT_MPEG2_PROFILE, eAVEncH264VProfile_Base);

    hr = m_encoder->SetOutputType(0, outType.Get(), 0);
    if (FAILED(hr)) return false;

    // Set Input Media Type (NV12)
    ComPtr<IMFMediaType> inType;
    hr = MFCreateMediaType(&inType);
    if (FAILED(hr)) return false;

    inType->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    inType->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_NV12);
    MFSetAttributeSize(inType.Get(), MF_MT_FRAME_SIZE, m_target_width, m_target_height);
    MFSetAttributeRatio(inType.Get(), MF_MT_FRAME_RATE, fps, 1);
    MFSetAttributeRatio(inType.Get(), MF_MT_PIXEL_ASPECT_RATIO, 1, 1);
    inType->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);

    hr = m_encoder->SetInputType(0, inType.Get(), 0);
    if (FAILED(hr)) return false;

    hr = m_encoder->ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0);
    hr = m_encoder->ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0);

    return true;
}

void H264Encoder::RequestKeyframe() {
    m_force_keyframe = true;
}

bool H264Encoder::EncodeFrame(ID3D11Texture2D* pTexture, uint32_t frame_id, std::vector<EncodedPacket>* out_packets) {
    if (!pTexture || !m_encoder || !out_packets) return false;

    // 1. GPU Color conversion & scaling: BGRA -> NV12
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

    // 2. Wrap NV12 texture in IMFMediaBuffer
    ComPtr<IMFMediaBuffer> inputBuffer;
    hr = MFCreateDXGISurfaceBuffer(
        __uuidof(ID3D11Texture2D),
        m_nv12_texture.Get(),
        0,
        FALSE,
        &inputBuffer
    );
    if (FAILED(hr)) return false;

    ComPtr<IMFSample> inputSample;
    hr = MFCreateSample(&inputSample);
    if (FAILED(hr)) return false;

    hr = inputSample->AddBuffer(inputBuffer.Get());
    if (FAILED(hr)) return false;

    inputSample->SetSampleTime(m_sample_time);
    inputSample->SetSampleDuration(m_frame_duration);
    m_sample_time += m_frame_duration;

    if (m_force_keyframe) {
        ComPtr<ICodecAPI> codecApi;
        if (SUCCEEDED(m_encoder.As(&codecApi))) {
            VARIANT var = {};
            var.vt = VT_UI4;
            var.ulVal = 1;
            codecApi->SetValue(&CODECAPI_AVEncVideoForceKeyFrame, &var);
        }
        m_force_keyframe = false;
    }

    // 3. Feed input into encoder
    hr = m_encoder->ProcessInput(0, inputSample.Get(), 0);
    if (FAILED(hr)) return false;

    // 4. Drain encoded output
    MFT_OUTPUT_STREAM_INFO streamInfo = {};
    m_encoder->GetOutputStreamInfo(0, &streamInfo);

    while (true) {
        MFT_OUTPUT_DATA_BUFFER outputData = {};
        outputData.dwStreamID = 0;

        ComPtr<IMFSample> outSample;
        hr = MFCreateSample(&outSample);
        if (FAILED(hr)) break;

        ComPtr<IMFMediaBuffer> outBuffer;
        hr = MFCreateMemoryBuffer(streamInfo.cbSize > 0 ? streamInfo.cbSize : 256 * 1024, &outBuffer);
        if (FAILED(hr)) break;

        outSample->AddBuffer(outBuffer.Get());
        outputData.pSample = outSample.Get();

        DWORD status = 0;
        hr = m_encoder->ProcessOutput(0, 1, &outputData, &status);
        if (hr == MF_E_TRANSFORM_NEED_MORE_INPUT) {
            break; // Finished draining for this input frame
        }
        if (FAILED(hr)) {
            break;
        }

        // Extract H.264 data from sample
        DWORD totalLength = 0;
        outputData.pSample->GetTotalLength(&totalLength);
        if (totalLength > 0) {
            BYTE* pData = nullptr;
            DWORD currentLength = 0;
            if (SUCCEEDED(outBuffer->Lock(&pData, nullptr, &currentLength)) && pData && currentLength > 0) {
                EncodedPacket packet = {};
                packet.data.assign(pData, pData + currentLength);
                packet.frame_id = frame_id;
                packet.timestamp_ms = static_cast<uint32_t>(m_sample_time / 10000);

                UINT32 cleanPoint = 0;
                outputData.pSample->GetUINT32(MFSampleExtension_CleanPoint, &cleanPoint);
                packet.is_keyframe = (cleanPoint != 0);

                out_packets->push_back(std::move(packet));
                outBuffer->Unlock();
            }
        }
    }

    return !out_packets->empty();
}

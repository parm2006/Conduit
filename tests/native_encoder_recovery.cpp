// Injects E_FAIL after a real encoder successfully consumes an
// output AU, faithfully reproducing lost output rather than network packet loss.
#include <windows.h>
#include <d3d11.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mftransform.h>
#include <wrl/client.h>
#include <vector>
#include <stdint.h>
#include <map>
#define private public
#include <h264_encoder.h>
#undef private
#include <h264_decoder.h>
#include <d3d10.h>
#include <atomic>
#include <cstdio>
#include <cstring>

class FailOneOutput final : public IMFTransform {
public:
    explicit FailOneOutput(IMFTransform* inner, bool consume) : inner_(inner), consume_(consume) {}
    void Arm() { armed_ = true; }
    unsigned faults = 0;
    std::vector<EncodedPacket> lost;

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        if (iid == __uuidof(IUnknown) || iid == __uuidof(IMFTransform)) {
            *out = static_cast<IMFTransform*>(this); AddRef(); return S_OK;
        }
        return inner_->QueryInterface(iid, out);
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG n = --refs_; if (!n) delete this; return n;
    }
    HRESULT STDMETHODCALLTYPE GetStreamLimits(DWORD* a, DWORD* b, DWORD* c, DWORD* d) override {
        return inner_->GetStreamLimits(a,b,c,d);
    }
    HRESULT STDMETHODCALLTYPE GetStreamCount(DWORD* a, DWORD* b) override { return inner_->GetStreamCount(a,b); }
    HRESULT STDMETHODCALLTYPE GetStreamIDs(DWORD a, DWORD* b, DWORD c, DWORD* d) override {
        return inner_->GetStreamIDs(a,b,c,d);
    }
    HRESULT STDMETHODCALLTYPE GetInputStreamInfo(DWORD a, MFT_INPUT_STREAM_INFO* b) override {
        return inner_->GetInputStreamInfo(a,b);
    }
    HRESULT STDMETHODCALLTYPE GetOutputStreamInfo(DWORD a, MFT_OUTPUT_STREAM_INFO* b) override {
        return inner_->GetOutputStreamInfo(a,b);
    }
    HRESULT STDMETHODCALLTYPE GetAttributes(IMFAttributes** a) override { return inner_->GetAttributes(a); }
    HRESULT STDMETHODCALLTYPE GetInputStreamAttributes(DWORD a, IMFAttributes** b) override {
        return inner_->GetInputStreamAttributes(a,b);
    }
    HRESULT STDMETHODCALLTYPE GetOutputStreamAttributes(DWORD a, IMFAttributes** b) override {
        return inner_->GetOutputStreamAttributes(a,b);
    }
    HRESULT STDMETHODCALLTYPE DeleteInputStream(DWORD a) override { return inner_->DeleteInputStream(a); }
    HRESULT STDMETHODCALLTYPE AddInputStreams(DWORD a, DWORD* b) override { return inner_->AddInputStreams(a,b); }
    HRESULT STDMETHODCALLTYPE GetInputAvailableType(DWORD a, DWORD b, IMFMediaType** c) override {
        return inner_->GetInputAvailableType(a,b,c);
    }
    HRESULT STDMETHODCALLTYPE GetOutputAvailableType(DWORD a, DWORD b, IMFMediaType** c) override {
        return inner_->GetOutputAvailableType(a,b,c);
    }
    HRESULT STDMETHODCALLTYPE SetInputType(DWORD a, IMFMediaType* b, DWORD c) override {
        return inner_->SetInputType(a,b,c);
    }
    HRESULT STDMETHODCALLTYPE SetOutputType(DWORD a, IMFMediaType* b, DWORD c) override {
        return inner_->SetOutputType(a,b,c);
    }
    HRESULT STDMETHODCALLTYPE GetInputCurrentType(DWORD a, IMFMediaType** b) override {
        return inner_->GetInputCurrentType(a,b);
    }
    HRESULT STDMETHODCALLTYPE GetOutputCurrentType(DWORD a, IMFMediaType** b) override {
        return inner_->GetOutputCurrentType(a,b);
    }
    HRESULT STDMETHODCALLTYPE GetInputStatus(DWORD a, DWORD* b) override { return inner_->GetInputStatus(a,b); }
    HRESULT STDMETHODCALLTYPE GetOutputStatus(DWORD* a) override { return inner_->GetOutputStatus(a); }
    HRESULT STDMETHODCALLTYPE SetOutputBounds(LONGLONG a, LONGLONG b) override { return inner_->SetOutputBounds(a,b); }
    HRESULT STDMETHODCALLTYPE ProcessEvent(DWORD a, IMFMediaEvent* b) override { return inner_->ProcessEvent(a,b); }
    HRESULT STDMETHODCALLTYPE ProcessMessage(MFT_MESSAGE_TYPE a, ULONG_PTR b) override {
        return inner_->ProcessMessage(a,b);
    }
    HRESULT STDMETHODCALLTYPE ProcessInput(DWORD a, IMFSample* b, DWORD c) override {
        return inner_->ProcessInput(a,b,c);
    }
    HRESULT STDMETHODCALLTYPE ProcessOutput(DWORD a, DWORD b, MFT_OUTPUT_DATA_BUFFER* c, DWORD* d) override {
        if (armed_ && !consume_) {
            armed_ = false; ++faults;
            std::puts("INJECTED_PENDING_OUTPUT_FAILURE");
            return E_FAIL;
        }
        const HRESULT hr = inner_->ProcessOutput(a,b,c,d);
        if (!armed_ || FAILED(hr) || !b || !c || !c[0].pSample) return hr;
        ComPtr<IMFMediaBuffer> buffer;
        if (FAILED(c[0].pSample->ConvertToContiguousBuffer(&buffer))) return hr;
        BYTE* bytes = nullptr; DWORD length = 0;
        if (FAILED(buffer->Lock(&bytes, nullptr, &length))) return hr;
        EncodedPacket packet{};
        packet.data.assign(bytes,bytes+length);
        buffer->Unlock();
        UINT32 clean = 0;
        c[0].pSample->GetUINT32(MFSampleExtension_CleanPoint,&clean);
        packet.is_keyframe = clean != 0;
        lost.push_back(std::move(packet));
        armed_ = false; ++faults;
        if (c[0].pEvents) { c[0].pEvents->Release(); c[0].pEvents = nullptr; }
        std::printf("INJECTED_REAL_OUTPUT_FAILURE bytes=%zu key=%d hr=%08lX\n",
                    lost.back().data.size(),lost.back().is_keyframe,E_FAIL);
        return E_FAIL;
    }
private:
    std::atomic<ULONG> refs_{1};
    ComPtr<IMFTransform> inner_;
    bool armed_ = false;
    bool consume_;
};

static bool HasIdr(const std::vector<uint8_t>& data) {
    for (size_t i=0;i+3<data.size();++i)
        if (!data[i] && !data[i+1] && data[i+2]==1 && (data[i+3]&31)==5) return true;
    return false;
}
struct Output { bool ok=false; uint64_t hash=0; HRESULT hr=E_FAIL; };
static Output Decode(H264Decoder& decoder, ID3D11Device* device, ID3D11DeviceContext* context,
                     const EncodedPacket& packet) {
    Output output;
    ComPtr<ID3D11Texture2D> texture; ComPtr<IMFSample> sample;
    uint32_t width=0,height=0,subresource=0;
    if (!decoder.DecodeFrame(packet.data.data(),packet.data.size(),&texture,&width,&height,&sample,
                             &subresource,nullptr,&output.hr)) return output;
    D3D11_TEXTURE2D_DESC desc{}; texture->GetDesc(&desc);
    desc.ArraySize=desc.MipLevels=1; desc.BindFlags=desc.MiscFlags=0;
    desc.Usage=D3D11_USAGE_STAGING; desc.CPUAccessFlags=D3D11_CPU_ACCESS_READ;
    ComPtr<ID3D11Texture2D> stage;
    if (FAILED(device->CreateTexture2D(&desc,nullptr,&stage))) return output;
    context->CopySubresourceRegion(stage.Get(),0,0,0,0,texture.Get(),subresource,nullptr);
    D3D11_MAPPED_SUBRESOURCE mapping{};
    if (FAILED(context->Map(stage.Get(),0,D3D11_MAP_READ,0,&mapping))) return output;
    output.hash=1469598103934665603ULL;
    for (uint32_t y=0;y<height;++y) for (uint32_t x=0;x<width;++x)
        output.hash=(output.hash^static_cast<uint8_t*>(mapping.pData)[y*mapping.RowPitch+x])*1099511628211ULL;
    output.ok=true; context->Unmap(stage.Get(),0); return output;
}
int main(int argc, char** argv) {
    const bool consume = argc < 2 || std::strcmp(argv[1], "pending") != 0;
    CoInitializeEx(nullptr,COINIT_MULTITHREADED); MFStartup(MF_VERSION);
    ComPtr<ID3D11Device> device,decode_device;
    ComPtr<ID3D11DeviceContext> context,decode_context;
    D3D_FEATURE_LEVEL level{};
    HRESULT hr=D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_HARDWARE,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        nullptr,0,D3D11_SDK_VERSION,&device,&level,&context);
    if (FAILED(hr)) { std::printf("DEVICE_FAIL %08lX\n",hr); return 2; }
    hr=D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_HARDWARE,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        nullptr,0,D3D11_SDK_VERSION,&decode_device,&level,&decode_context);
    if (FAILED(hr)) { std::printf("DECODE_DEVICE_FAIL %08lX\n",hr); return 2; }
    ComPtr<ID3D10Multithread> protect; decode_context.As(&protect);
    if (protect) protect->SetMultithreadProtected(TRUE);
    constexpr unsigned width=1920,height=1080;
    D3D11_TEXTURE2D_DESC desc{}; desc.Width=width; desc.Height=height;
    desc.MipLevels=desc.ArraySize=1; desc.Format=DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count=1; desc.Usage=D3D11_USAGE_DEFAULT;
    desc.BindFlags=D3D11_BIND_RENDER_TARGET|D3D11_BIND_SHADER_RESOURCE;
    ComPtr<ID3D11Texture2D> input; device->CreateTexture2D(&desc,nullptr,&input);
    H264Encoder encoder; H264Decoder reference,receiver;
    if (!input || !encoder.Initialize(device.Get(),width,height,width,height,60,10000) ||
        !reference.Initialize(decode_device.Get(),width,height,true) ||
        !receiver.Initialize(decode_device.Get(),width,height,true)) { std::printf("INIT_FAIL\n"); return 2; }
    auto* proxy=new FailOneOutput(encoder.m_encoder.Get(),consume);
    encoder.m_encoder.Attach(proxy); // Attach releases the previous pointer; proxy owns its independent reference.
    std::vector<uint32_t> pixels(width*height); std::vector<EncodedPacket> packets;
    unsigned encoded=0,no_output=0,decode_failures=0,mismatches=0,assertions=0,post_fault_outputs=0;
    std::map<uint32_t, int> expected_cursor_modes;
    bool injected=false,next_after_fault=false;
    for (unsigned i=0;i<80;++i) {
        const uint8_t gray=uint8_t(40+(i%5)*40);
        for (unsigned y=0;y<height;++y) for (unsigned x=0;x<width;++x) {
            const uint8_t g=uint8_t(gray+((x/16+y/16+i/3)%2)*12);
            pixels[y*width+x]=0xff000000u|unsigned(g)*0x010101u;
        }
        context->UpdateSubresource(input.Get(),0,nullptr,pixels.data(),width*4,0);
        if (i==30) proxy->Arm();
        packets.clear();
        const int cursor_mode = static_cast<int>(i % 3) - 1;
        expected_cursor_modes[static_cast<uint32_t>(encoder.m_sample_time / 10000)] = cursor_mode;
        const bool emitted=encoder.EncodeFrame(input.Get(),i+1,&packets,cursor_mode);
        if (!emitted) ++no_output;
        if (proxy->faults && !injected) {
            injected = true; next_after_fault = true;
            if (encoder.GetLastError() != E_FAIL) ++assertions;
        }
        for (const auto& lost:proxy->lost) {
            if (encoder.GetLastError() != E_FAIL) ++assertions;
            const Output ref=Decode(reference,decode_device.Get(),decode_context.Get(),lost);
            if (!ref.ok) { ++assertions; std::printf("LOST_REFERENCE_DECODE_FAILED %08lX\n",ref.hr); }
            injected=true; next_after_fault=true;
        }
        proxy->lost.clear();
        for (const auto& packet:packets) {
            const auto mode = expected_cursor_modes.find(packet.timestamp_ms);
            if (mode == expected_cursor_modes.end() || packet.cursor_in_frame != mode->second) {
                ++assertions;
                std::printf("ENCODER_CURSOR_METADATA_MISMATCH input=%u\n",i);
            }
            ++encoded;
            if (next_after_fault) {
                const bool idr=HasIdr(packet.data);
                std::printf("NEXT_AFTER_OUTPUT_FAILURE input=%u cleanpoint=%d idr=%d\n",i,packet.is_keyframe,idr);
                if (!packet.is_keyframe || !idr) ++assertions;
                next_after_fault=false;
            }
            const auto ref=Decode(reference,decode_device.Get(),decode_context.Get(),packet);
            const auto rx=Decode(receiver,decode_device.Get(),decode_context.Get(),packet);
            if (!ref.ok || !rx.ok) { ++decode_failures; continue; }
            if (injected) ++post_fault_outputs;
            if (ref.hash!=rx.hash) {
                ++mismatches;
                std::printf("DECODED_PIXEL_MISMATCH input=%u key=%d decoder_hr=%08lX reference=%llu receiver=%llu\n",
                            i,packet.is_keyframe,rx.hr,ref.hash,rx.hash);
            }
        }
    }
    if (proxy->faults!=1 || !post_fault_outputs || next_after_fault) ++assertions;
    std::printf("ENCODER_ERROR_RESULT faults=%u emitted=%u no_output=%u post_fault_outputs=%u mismatches=%u decode_failures=%u assertions=%u\n",
                proxy->faults,encoded,no_output,post_fault_outputs,mismatches,decode_failures,assertions);
    encoder.Cleanup(); reference.Cleanup(); receiver.Cleanup();
    MFShutdown(); CoUninitialize();
    return assertions || mismatches || decode_failures ? 1 : 0;
}

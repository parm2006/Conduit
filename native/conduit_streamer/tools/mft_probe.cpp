// mft_probe.cpp - run this on BOTH PCs first. It reports which H.264 encoder/decoder
// MFTs exist, whether each is hardware, async, D3D11-aware, and which low-latency
// ICodecAPI knobs it claims to support. Nothing here is a latency measurement; it
// replaces guessing about the hardware path.
//
// Build: cl /std:c++17 /EHsc /W4 mft_probe.cpp /link mfplat.lib mfuuid.lib strmiids.lib ole32.lib
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mferror.h>
#include <mftransform.h>
#include <strmif.h>
#include <codecapi.h>
#include <wrl/client.h>

#include <cstdio>

using Microsoft::WRL::ComPtr;

static const char* Sup(ICodecAPI* api, const GUID& g) {
    if (!api) return "n/a";
    const HRESULT hr = api->IsSupported(&g);
    return hr == S_OK ? "yes" : (hr == S_FALSE ? "no" : "?");
}

static bool Probe(const char* title, const GUID& category, UINT32 flags, const GUID& in_sub,
                  const GUID& out_sub) {
    MFT_REGISTER_TYPE_INFO in{MFMediaType_Video, in_sub};
    MFT_REGISTER_TYPE_INFO out{MFMediaType_Video, out_sub};
    IMFActivate** act = nullptr;
    UINT32 n = 0;
    const HRESULT hr = MFTEnumEx(category, flags, &in, &out, &act, &n);
    std::printf("\n== %s (hr=0x%08lX, %u found)\n", title, static_cast<unsigned long>(hr), n);
    if (FAILED(hr)) return false;

    for (UINT32 i = 0; i < n; ++i) {
        WCHAR* name = nullptr;
        WCHAR* vendor = nullptr;
        UINT32 len = 0;
        if (FAILED(act[i]->GetAllocatedString(MFT_FRIENDLY_NAME_Attribute, &name, &len))) name = nullptr;
        if (FAILED(act[i]->GetAllocatedString(MFT_ENUM_HARDWARE_VENDOR_ID_Attribute, &vendor, &len)))
            vendor = nullptr;
        std::printf("  [%u] %ls  (hw vendor id: %ls)\n", i, name ? name : L"?", vendor ? vendor : L"-");

        ComPtr<IMFTransform> mft;
        const HRESULT ahr = act[i]->ActivateObject(IID_PPV_ARGS(&mft));
        if (FAILED(ahr)) {
            std::printf("      ActivateObject failed: 0x%08lX\n", static_cast<unsigned long>(ahr));
        } else {
            ComPtr<IMFAttributes> attrs;
            if (SUCCEEDED(mft->GetAttributes(&attrs)) && attrs) {
                std::printf("      async=%u  d3d11_aware=%u\n",
                            MFGetAttributeUINT32(attrs.Get(), MF_TRANSFORM_ASYNC, 0),
                            MFGetAttributeUINT32(attrs.Get(), MF_SA_D3D11_AWARE, 0));
            } else {
                std::printf("      (no attribute store)\n");
            }
            ComPtr<ICodecAPI> api;
            mft.As(&api);
            std::printf("      codecapi: CommonLowLatency=%s AVLowLatencyMode=%s ForceKeyFrame=%s "
                        "LTRBuffer=%s SliceControl=%s\n",
                        Sup(api.Get(), CODECAPI_AVEncCommonLowLatency),
                        Sup(api.Get(), CODECAPI_AVLowLatencyMode),
                        Sup(api.Get(), CODECAPI_AVEncVideoForceKeyFrame),
                        Sup(api.Get(), CODECAPI_AVEncVideoLTRBufferControl),
                        Sup(api.Get(), CODECAPI_AVEncSliceControlMode));
        }
        act[i]->ShutdownObject();
        if (name) CoTaskMemFree(name);
        if (vendor) CoTaskMemFree(vendor);
        act[i]->Release();
    }
    CoTaskMemFree(act);
    return true;
}

int wmain() {
    const HRESULT com = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(com)) { std::printf("COM initialization failed: 0x%08lX\n", static_cast<unsigned long>(com)); return 1; }
    const HRESULT mf = MFStartup(MF_VERSION);
    if (FAILED(mf)) { std::printf("MFStartup failed: 0x%08lX\n", static_cast<unsigned long>(mf)); CoUninitialize(); return 1; }
    bool ok = true;

    const UINT32 hw = MFT_ENUM_FLAG_HARDWARE | MFT_ENUM_FLAG_SORTANDFILTER;
    const UINT32 sw = MFT_ENUM_FLAG_SYNCMFT | MFT_ENUM_FLAG_SORTANDFILTER;

    ok = Probe("H.264 ENCODERS - hardware MFTs (vendor, usually async)", MFT_CATEGORY_VIDEO_ENCODER, hw,
          MFVideoFormat_NV12, MFVideoFormat_H264) && ok;
    ok = Probe("H.264 ENCODERS - software/sync MFTs (CLSID_CMSH264EncoderMFT is expected here)",
          MFT_CATEGORY_VIDEO_ENCODER, sw, MFVideoFormat_NV12, MFVideoFormat_H264) && ok;
    ok = Probe("H.264 DECODERS - hardware MFTs", MFT_CATEGORY_VIDEO_DECODER, hw, MFVideoFormat_H264,
          MFVideoFormat_NV12) && ok;
    ok = Probe("H.264 DECODERS - sync MFTs (Microsoft decoder uses DXVA only if d3d11_aware=1 AND a D3D "
          "manager is accepted at runtime)",
          MFT_CATEGORY_VIDEO_DECODER, sw, MFVideoFormat_H264, MFVideoFormat_NV12) && ok;

    MFShutdown();
    CoUninitialize();
    return ok ? 0 : 1;
}

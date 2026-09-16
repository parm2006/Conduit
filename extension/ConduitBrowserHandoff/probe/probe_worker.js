// Disposable diagnostic probe. Keep it free of tabs, URLs, titles, and storage.
const nativePort = chrome.runtime.connectNative("com.conduit.browser_handoff_probe");
const browserInstanceId = crypto.randomUUID();
let browserProcess = null;

function windowMetadata(window) {
  return {
    window_id: window.id,
    focused: window.focused === true,
    incognito: window.incognito === true,
    state: window.state,
    browser_process_id: browserProcess?.id ?? null,
    browser_process_created: browserProcess?.created ?? null,
    // Chromium documents these as extension screen units. The physical-pixel
    // conversion is measured in the plan's dedicated DPI experiment.
    left: window.left,
    top: window.top,
    width: window.width,
    height: window.height,
  };
}

async function publishWindows(reason) {
  try {
    const windows = await chrome.windows.getAll({ populate: false });
    nativePort.postMessage({
      type: "window_metadata",
      browser_instance_id: browserInstanceId,
      reason,
      coordinate_units: "extension_screen_units_unverified",
      captured_monotonic: performance.now(),
      windows: windows.map(windowMetadata),
    });
  } catch (error) {
    nativePort.postMessage({ type: "probe_error", reason: "windows_api_failed" });
  }
}

nativePort.onMessage.addListener((message) => {
  if (message.type === "probe_host_ready") {
    browserProcess = {
      id: message.browser_process_id,
      created: message.browser_process_created,
    };
    publishWindows("native_ready");
  }
});
nativePort.onDisconnect.addListener(() => {});
nativePort.postMessage({ type: "probe_hello", browser_instance_id: browserInstanceId });
chrome.windows.onCreated.addListener(() => publishWindows("created"));
chrome.windows.onRemoved.addListener(() => publishWindows("removed"));
chrome.windows.onFocusChanged.addListener(() => publishWindows("focus"));
chrome.windows.onBoundsChanged.addListener(() => publishWindows("bounds"));
publishWindows("startup");

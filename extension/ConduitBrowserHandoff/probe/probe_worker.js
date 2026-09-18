// Disposable diagnostic probe. Keep it free of tabs, URLs, titles, and storage.

export function installProbeWorker(chrome, {
  instanceId = crypto.randomUUID(),
} = {}) {
  const nativePort = chrome.runtime.connectNative("com.conduit.browser_handoff_probe");
  let browserProcess = null;
  let lastCorrelation = null;
  let lastDiagnostics = null;
  let connection = "connecting";
  const workerDiagnostics = {
    native_messages_received: 0,
    diagnostics_received: 0,
    diagnostics_stale: false,
  };

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
        browser_instance_id: instanceId,
        reason,
        coordinate_units: "extension_screen_units_unverified",
        captured_monotonic: performance.now(),
        windows: windows.map(windowMetadata),
      });
    } catch {
      nativePort.postMessage({ type: "probe_error", reason: "windows_api_failed" });
    }
  }

  nativePort.onMessage.addListener((message) => {
    workerDiagnostics.native_messages_received += 1;
    if (message?.type === "probe_host_ready") {
      connection = "ready";
      browserProcess = {
        id: message.browser_process_id,
        created: message.browser_process_created,
      };
      void publishWindows("native_ready");
      return;
    }
    if (message?.type === "probe_diagnostics") {
      workerDiagnostics.diagnostics_received += 1;
      workerDiagnostics.diagnostics_stale = false;
      lastDiagnostics = message;
      return;
    }
    if (message?.type === "probe_correlation" || message?.type === "probe_error") {
      lastCorrelation = message;
    }
  });
  nativePort.onDisconnect.addListener(() => {
    connection = "disconnected";
    workerDiagnostics.diagnostics_stale = true;
    lastCorrelation = { type: "probe_error", reason: "native_host_disconnected" };
  });
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type !== "probe_status") return undefined;
    respond({
      connection,
      correlation: lastCorrelation,
      diagnostics: lastDiagnostics,
      worker: { ...workerDiagnostics },
    });
    return true;
  });
  for (const event of [
    chrome.windows.onCreated,
    chrome.windows.onRemoved,
    chrome.windows.onFocusChanged,
    chrome.windows.onBoundsChanged,
  ]) event.addListener(() => { void publishWindows("window_change"); });
  nativePort.postMessage({ type: "probe_hello", browser_instance_id: instanceId });
  void publishWindows("startup");
  return {
    latestCorrelation: () => lastCorrelation,
    status: () => ({
      connection,
      correlation: lastCorrelation,
      diagnostics: lastDiagnostics,
      worker: { ...workerDiagnostics },
    }),
  };
}

if (typeof chrome !== "undefined" && chrome.runtime?.connectNative) {
  installProbeWorker(chrome);
}

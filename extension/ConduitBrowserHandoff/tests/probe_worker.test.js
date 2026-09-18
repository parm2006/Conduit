import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { installProbeWorker } from "../probe/probe_worker.js";
import * as probeStatus from "../probe/probe_status.js";

function event() {
  return { addListener(listener) { this.listener = listener; } };
}

test("keeps the latest URL-free correlation result in memory for the probe popup", () => {
  const port = {
    onMessage: event(),
    onDisconnect: event(),
    postMessage() {},
  };
  const chrome = {
    runtime: { connectNative() { return port; }, onMessage: event() },
    windows: {
      getAll: async () => [],
      onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event(),
    },
  };
  const worker = installProbeWorker(chrome, { instanceId: "opaque-instance" });
  const evidence = {
    type: "probe_correlation",
    status: "unique_raw_match",
    native: { process_id: 101, process_created: 1234, bounds: [1, 2, 3, 4] },
    candidates: [],
  };

  port.onMessage.listener(evidence);

  assert.deepEqual(worker.latestCorrelation(), evidence);
  let response;
  assert.equal(
    chrome.runtime.onMessage.listener({ type: "probe_status" }, null, (value) => { response = value; }),
    true,
  );
  assert.deepEqual(response, {
    connection: "connecting",
    correlation: evidence,
    diagnostics: null,
    worker: { native_messages_received: 1, diagnostics_received: 0, diagnostics_stale: false },
  });
});

test("reports a disconnected native host instead of leaving the popup waiting", () => {
  const port = {
    onMessage: event(),
    onDisconnect: event(),
    postMessage() {},
  };
  const chrome = {
    runtime: { connectNative() { return port; }, onMessage: event() },
    windows: {
      getAll: async () => [],
      onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event(),
    },
  };
  const worker = installProbeWorker(chrome, { instanceId: "opaque-instance" });

  port.onDisconnect.listener();

  assert.deepEqual(worker.status(), {
    connection: "disconnected",
    correlation: { type: "probe_error", reason: "native_host_disconnected" },
    diagnostics: null,
    worker: { native_messages_received: 0, diagnostics_received: 0, diagnostics_stale: true },
  });
});

test("reports diagnostics even when no correlation result exists", () => {
  const port = {
    onMessage: event(),
    onDisconnect: event(),
    postMessage() {},
  };
  const chrome = {
    runtime: { connectNative() { return port; }, onMessage: event() },
    windows: {
      getAll: async () => [],
      onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event(),
    },
  };
  const worker = installProbeWorker(chrome, { instanceId: "opaque-instance" });
  const diagnostics = {
    type: "probe_diagnostics",
    host: { last_stage: "metadata_received" },
    observer: { raw_callbacks: 0 },
  };

  port.onMessage.listener(diagnostics);

  assert.deepEqual(worker.status(), {
    connection: "connecting",
    correlation: null,
    diagnostics,
    worker: { native_messages_received: 1, diagnostics_received: 1, diagnostics_stale: false },
  });
});

test("marks retained diagnostics stale when the native host disconnects", () => {
  const port = {
    onMessage: event(),
    onDisconnect: event(),
    postMessage() {},
  };
  const chrome = {
    runtime: { connectNative() { return port; }, onMessage: event() },
    windows: {
      getAll: async () => [],
      onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event(),
    },
  };
  const worker = installProbeWorker(chrome, { instanceId: "opaque-instance" });
  port.onMessage.listener({
    type: "probe_diagnostics",
    host: { last_stage: "metadata_accepted" },
    observer: { running: true },
  });

  port.onDisconnect.listener();
  const snapshot = worker.status();

  assert.equal(snapshot.connection, "disconnected");
  assert.equal(snapshot.worker.diagnostics_stale, true);
  assert.equal(probeStatus.probeHeadline(snapshot), "Probe host: disconnected.");
});

test("popup headline distinguishes missing diagnostics from waiting for correlation", () => {
  assert.equal(typeof probeStatus.probeHeadline, "function");
  assert.equal(
    probeStatus.probeHeadline({ connection: "ready", correlation: null, diagnostics: null }),
    "Connected, but diagnostic data has not arrived.",
  );
  assert.equal(
    probeStatus.probeHeadline({
      connection: "ready",
      correlation: null,
      diagnostics: { host: { last_stage: "metadata_received" } },
    }),
    "Connected. Last confirmed stage: metadata_received.",
  );
});

test("probe manifest exposes a local status popup without tab or storage permission", async () => {
  const manifest = JSON.parse(await readFile(new URL("../probe/manifest.json", import.meta.url)));

  assert.equal(manifest.action.default_popup, "popup.html");
  assert.deepEqual(manifest.permissions.sort(), ["nativeMessaging", "windows"]);
});

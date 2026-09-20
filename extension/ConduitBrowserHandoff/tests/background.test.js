import test from "node:test";
import assert from "node:assert/strict";

import { MetadataCoalescer, RequestCoordinator, installWorker, resultEnvelope } from "../background.js";

function connectionHarness(options = {}) {
  const timers = [], ports = [];
  const event = () => ({ addListener(listener) { this.listener = listener; } });
  const chrome = {
    runtime: { onMessage: event(), connectNative() {
      const port = { onMessage: event(), onDisconnect: event(), messages: [],
        postMessage(message) { this.messages.push(message); } };
      ports.push(port);
      return port;
    } },
    windows: { onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event(), async getAll() { return []; } },
    tabs: { onCreated: event(), onRemoved: event(), onMoved: event(), onAttached: event(), onDetached: event(), onReplaced: event(), onUpdated: event() },
  };
  installWorker(chrome, { epoch: "instance", setTimeoutFn: (callback, delay) => timers.push({ callback, delay }), ...options });
  const status = () => new Promise(resolve => chrome.runtime.onMessage.listener({ type: "browser_handoff_status" }, {}, resolve));
  return { chrome, timers, ports, status };
}

test("connected status requires a completed desktop handshake", async () => {
  const h = connectionHarness();
  assert.equal((await h.status()).connected, false);
  await h.ports[0].onMessage.listener({ type: "bridge_ready", bridge_epoch: "desktop" });
  assert.equal((await h.status()).connected, true);
  h.ports[0].onDisconnect.listener();
  assert.equal((await h.status()).connected, false);
});

test("successful handshake resets consecutive reconnect failures", async () => {
  const h = connectionHarness({ maxReconnects: 1 });
  h.ports[0].onDisconnect.listener();
  h.timers.shift().callback();
  await h.ports[1].onMessage.listener({ type: "bridge_ready", bridge_epoch: "desktop" });
  h.ports[1].onDisconnect.listener();
  assert.equal(h.timers.length, 1);
  assert.equal(h.timers[0].delay, 250);
});

test("desktop pipe loss disconnects the native port so bounded reconnect can run", async () => {
  const timers = [];
  let disconnected = 0;
  const port = { onMessage: { addListener(listener) { this.listener = listener; } }, onDisconnect: { addListener(listener) { this.listener = listener; } },
    postMessage() {}, disconnect() { disconnected += 1; } };
  const event = () => ({ addListener() {} });
  const chrome = {
    runtime: { connectNative() { return port; } },
    windows: { onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event() },
    tabs: { onCreated: event(), onRemoved: event(), onMoved: event(), onAttached: event(), onDetached: event(), onReplaced: event(), onUpdated: event() },
  };
  installWorker(chrome, { epoch: "instance-1", setTimeoutFn: (callback, delay) => timers.push({ callback, delay }) });
  await port.onMessage.listener({ type: "browser_handoff_error", reason: "bridge_disconnected" });
  assert.equal(disconnected, 1);
  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 250);
});

test("explicit refresh queries fresh bounds and echoes its request without relabelling an older query", async () => {
  const responses = [];
  const queries = [];
  const listeners = [];
  const port = { onMessage: { addListener(listener) { this.listener = listener; } }, onDisconnect: { addListener() {} }, postMessage(value) { responses.push(value); } };
  const event = () => ({ addListener(listener) { listeners.push(listener); } });
  const chrome = {
    runtime: { connectNative() { return port; } },
    windows: { onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event(), getAll(options) {
      assert.deepEqual(options, { populate: false });
      return new Promise(resolve => queries.push(resolve));
    } },
    tabs: { onCreated: event(), onRemoved: event(), onMoved: event(), onAttached: event(), onDetached: event(), onReplaced: event(), onUpdated: event() },
  };
  installWorker(chrome, { epoch: "instance-1" });
  await port.onMessage.listener({ type: "bridge_ready", bridge_epoch: "bridge-1" });
  assert.equal(queries.length, 1);
  const refreshing = port.onMessage.listener({ type: "browser_handoff_metadata_request", request_id: "fresh-query" });
  assert.equal(queries.length, 2);
  queries[1]([{ id: 7, left: 500, top: 200, width: 800, height: 700, incognito: false }]);
  await refreshing;
  const fresh = responses.at(-1);
  assert.equal(fresh.request_id, "fresh-query");
  assert.equal(fresh.revision, 2);
  assert.equal(fresh.windows[0].left, 500);
  queries[0]([{ id: 7, left: -8, top: -8, width: 1920, height: 1080 }]);
  await new Promise(resolve => queueMicrotask(resolve));
  assert.equal(responses.at(-1).revision, 1);
  assert.equal(responses.at(-1).request_id, undefined);
  assert.equal(JSON.stringify(responses).includes("url"), false);
});

test("drag bounds changes publish geometry without invalidating a stable tab snapshot", async () => {
  const h = connectionHarness();
  const port = h.ports[0];
  await port.onMessage.listener({ type: "bridge_ready", bridge_epoch: "desktop" });
  await port.onMessage.listener({ type: "browser_handoff_metadata_request", request_id: "moving" });
  const matchedRevision = port.messages.find(m => m.request_id === "moving").revision;
  h.chrome.windows.onBoundsChanged.listener();
  h.chrome.windows.get = async () => {
    h.chrome.windows.onBoundsChanged.listener();
    return { id: 7, incognito: false, tabs: [{ url: "https://example.test", active: true }] };
  };
  await port.onMessage.listener({ type: "browser_handoff_snapshot_request", window_id: 7, request_id: "snapshot" });
  const snapshot = port.messages.find(m => m.request_id === "snapshot").snapshot;
  assert.equal(snapshot.complete_capture, true);
  assert.equal(snapshot.revision, matchedRevision);
  assert.ok(port.messages.filter(m => m.type === "browser_handoff_metadata").length >= 3);
  h.chrome.tabs.onUpdated.listener();
  await port.onMessage.listener({ type: "browser_handoff_snapshot_request", window_id: 7, request_id: "changed" });
  assert.notEqual(port.messages.find(m => m.request_id === "changed").snapshot.revision, matchedRevision);
});

test("coalesces an event burst into one metadata publish", async () => {
  const callbacks = [];
  let publishes = 0;
  const coalescer = new MetadataCoalescer({ schedule: (callback) => callbacks.push(callback), publish: async () => { publishes += 1; } });

  coalescer.request();
  coalescer.request();
  assert.equal(callbacks.length, 1);
  await callbacks[0]();
  assert.equal(publishes, 1);
});

test("uses queueMicrotask without binding the browser global as a method", () => {
  const original = globalThis.queueMicrotask;
  let invoked = false;
  globalThis.queueMicrotask = function (callback) {
    assert.equal(this, undefined);
    invoked = true;
    callback();
  };
  try {
    const coalescer = new MetadataCoalescer({ publish: async () => {} });
    coalescer.request();
    assert.equal(invoked, true);
  } finally {
    globalThis.queueMicrotask = original;
  }
});

test("normalizes internal outcomes into the URL-free result envelope", () => {
  const envelope = resultEnvelope("a".repeat(32), "epoch-1", "ticket-1", {
    status: "partial", opened_count: 1, total_count: 2,
    outcomes: [{ source_index: 1, reason: "blocked_scheme" }],
  });

  assert.equal(envelope.type, "browser_handoff_result");
  assert.deepEqual(envelope.entries, [{ source_index: 1, reason: "blocked_scheme" }]);
  assert.equal(envelope.outcomes, undefined);
});

test("claims duplicate concurrent requests before opening a destination window", async () => {
  let release;
  let calls = 0;
  const opened = new Promise((resolve) => { release = resolve; });
  const coordinator = new RequestCoordinator({
    open: async () => { calls += 1; await opened; return { status: "complete", opened_count: 2, total_count: 2, outcomes: [] }; },
    now: () => 100,
  });
  const request = { request_id: "a".repeat(32) };

  const first = coordinator.start("epoch-1", request);
  const duplicate = await coordinator.start("epoch-1", request);

  assert.equal(duplicate.status, "pending");
  assert.equal(calls, 1);
  release();
  assert.equal((await first).status, "complete");
  assert.equal((await coordinator.start("epoch-1", request)).status, "complete");
  assert.equal(calls, 1);
});

test("marks elapsed opens unknown without automatically retrying", async () => {
  let now = 0;
  let calls = 0;
  const coordinator = new RequestCoordinator({
    open: async () => { calls += 1; return new Promise(() => {}); },
    now: () => now,
    deadlineMs: 10,
  });
  const request = { request_id: "b".repeat(32), total_count: 2 };

  void coordinator.start("epoch-1", request);
  now = 11;
  const result = await coordinator.start("epoch-1", request);

  assert.equal(result.status, "unknown");
  assert.equal(result.total_count, 2);
  assert.equal(calls, 1);
});

test("registers metadata listeners synchronously and returns a captured snapshot", async () => {
  const listeners = [];
  const responses = [];
  const port = { onMessage: { addListener(listener) { this.listener = listener; } }, onDisconnect: { addListener(listener) { this.listener = listener; } }, postMessage(value) { responses.push(value); } };
  const event = () => ({ addListener(listener) { listeners.push(listener); } });
  const chrome = {
    runtime: { connectNative() { return port; } },
    windows: { onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event(), async get() { return { incognito: false, tabs: [{ id: 1, url: "https://example.test", active: true }] }; } },
    tabs: { onCreated: event(), onRemoved: event(), onMoved: event(), onAttached: event(), onDetached: event(), onReplaced: event(), onUpdated: event() },
  };

  installWorker(chrome, { epoch: "epoch-1" });
  assert.equal(listeners.length, 11);
  assert.deepEqual(responses[0], {
    type: "browser_handoff_hello", browser_instance_id: "epoch-1",
  });
  await port.onMessage.listener({ type: "browser_handoff_snapshot_request", window_id: 7, request_id: "c".repeat(32) });

  assert.equal(responses[1].type, "browser_handoff_snapshot");
  assert.equal(responses[1].snapshot.entries[0].source_index, 0);
});

test("metadata contains no URLs and stays bound to the native-host browser instance", async () => {
  const responses = [];
  const listeners = [];
  const port = { onMessage: { addListener(listener) { this.listener = listener; } }, onDisconnect: { addListener() {} }, postMessage(value) { responses.push(value); } };
  const event = () => ({ addListener(listener) { listeners.push(listener); } });
  const chrome = {
    runtime: { connectNative() { return port; } },
    windows: { onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event(), async getAll() { return [{ id: 7, left: 1, top: 2, width: 3, height: 4, focused: true, incognito: false, state: "normal" }]; } },
    tabs: { onCreated: event(), onRemoved: event(), onMoved: event(), onAttached: event(), onDetached: event(), onReplaced: event(), onUpdated: event() },
  };

  installWorker(chrome, { epoch: "instance-1" });
  listeners[0]();
  await new Promise((resolve) => queueMicrotask(resolve));

  assert.deepEqual(responses[1], {
    type: "browser_handoff_metadata", epoch: "instance-1", browser_instance_id: "instance-1",
    revision: 1, coordinate_units: "browser_dip", windows: [{
      window_id: 7, focused: true, incognito: false, state: "normal",
      left: 1, top: 2, width: 3, height: 4,
    }],
  });
});

test("publishes initial metadata when the desktop bridge becomes ready", async () => {
  const responses = [];
  const port = { onMessage: { addListener(listener) { this.listener = listener; } }, onDisconnect: { addListener() {} }, postMessage(value) { responses.push(value); } };
  const event = () => ({ addListener() {} });
  const chrome = {
    runtime: { connectNative() { return port; } },
    windows: { onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event(), async getAll() { return [{ id: 7, left: 1, top: 2, width: 3, height: 4, focused: true, incognito: false, state: "normal" }]; } },
    tabs: { onCreated: event(), onRemoved: event(), onMoved: event(), onAttached: event(), onDetached: event(), onReplaced: event(), onUpdated: event() },
  };

  installWorker(chrome, { epoch: "instance-1" });
  await port.onMessage.listener({ type: "bridge_ready", bridge_epoch: "desktop-epoch" });
  await new Promise((resolve) => queueMicrotask(resolve));

  assert.equal(responses[1].type, "browser_handoff_capabilities");
  assert.deepEqual(responses[2], {
    type: "browser_handoff_metadata", epoch: "instance-1", browser_instance_id: "instance-1",
    revision: 1, coordinate_units: "browser_dip", windows: [{
      window_id: 7, focused: true, incognito: false, state: "normal",
      left: 1, top: 2, width: 3, height: 4,
    }],
  });
});

test("uses the desktop-issued bridge epoch for a receiver result", async () => {
  const responses = [];
  const port = { onMessage: { addListener(listener) { this.listener = listener; } }, onDisconnect: { addListener() {} }, postMessage(value) { responses.push(value); } };
  const event = () => ({ addListener() {} });
  const chrome = {
    runtime: { connectNative() { return port; } },
    windows: { onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event(), async create() { return { id: 9, incognito: false }; }, async remove() {}, async update() {} },
    tabs: { onCreated: event(), onRemoved: event(), onMoved: event(), onAttached: event(), onDetached: event(), onReplaced: event(), onUpdated: event(), async create() { return { id: 4 }; } },
  };
  installWorker(chrome, { epoch: "browser-epoch" });
  await port.onMessage.listener({ type: "bridge_ready", bridge_epoch: "desktop-epoch" });
  assert.deepEqual(responses[1], {
    type: "browser_handoff_capabilities", browser_instance_id: "browser-epoch", receiver_epoch: "desktop-epoch",
  });
  await port.onMessage.listener({ type: "browser_handoff_request", request: {
    protocol: 1, request_id: "a".repeat(32), route_ticket: "ticket",
    topology_version: 1, destination_machine_id: "client-b", incognito: false,
    total_count: 1, entries: [{ source_index: 0, url: "https://example.test", active: true }], complete_capture: true,
  } });
  assert.equal(responses.at(-1).receiver_epoch, "desktop-epoch");
});

test("reconnects the native channel with bounded backoff without replaying requests", () => {
  const timers = [];
  const ports = [];
  const event = () => ({ addListener() {} });
  const chrome = {
    runtime: { connectNative() { const port = { onMessage: { addListener(listener) { this.listener = listener; } }, onDisconnect: { addListener(listener) { this.listener = listener; } }, postMessage() {} }; ports.push(port); return port; } },
    windows: { onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event() },
    tabs: { onCreated: event(), onRemoved: event(), onMoved: event(), onAttached: event(), onDetached: event(), onReplaced: event(), onUpdated: event() },
  };

  installWorker(chrome, { epoch: "epoch-1", maxReconnects: 2, setTimeoutFn: (callback, delay) => timers.push({ callback, delay }) });
  ports[0].onDisconnect.listener();
  assert.equal(timers[0].delay, 250);
  timers.shift().callback();
  ports[1].onDisconnect.listener();
  assert.equal(timers[0].delay, 500);
  timers.shift().callback();
  ports[2].onDisconnect.listener();

  assert.equal(ports.length, 3);
  assert.equal(timers.length, 0);
});

test("consumes the native disconnect error before scheduling a reconnect", () => {
  const timers = [];
  const port = { onMessage: { addListener() {} }, onDisconnect: { addListener(listener) { this.listener = listener; } }, postMessage() {} };
  const event = () => ({ addListener() {} });
  let lastErrorReads = 0;
  const runtime = { connectNative() { return port; } };
  Object.defineProperty(runtime, "lastError", {
    get() { lastErrorReads += 1; return { message: "Native host has exited." }; },
  });
  const chrome = {
    runtime,
    windows: { onCreated: event(), onRemoved: event(), onFocusChanged: event(), onBoundsChanged: event() },
    tabs: { onCreated: event(), onRemoved: event(), onMoved: event(), onAttached: event(), onDetached: event(), onReplaced: event(), onUpdated: event() },
  };

  installWorker(chrome, { epoch: "epoch-1", setTimeoutFn: (callback, delay) => timers.push({ callback, delay }) });
  port.onDisconnect.listener();

  assert.equal(lastErrorReads, 1);
  assert.equal(timers[0].delay, 250);
});

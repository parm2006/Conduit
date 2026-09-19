import test from "node:test";
import assert from "node:assert/strict";

import { MetadataCoalescer, RequestCoordinator, installWorker, resultEnvelope } from "../background.js";

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

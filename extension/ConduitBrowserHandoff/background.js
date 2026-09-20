import { openDestinationWindow } from "./open_window.js";
import { captureWindow } from "./snapshot.js";
import { validateRequest } from "./protocol.js";

export class MetadataCoalescer {
  constructor({ schedule = (callback) => queueMicrotask(callback), publish }) {
    this.schedule = schedule;
    this.publish = publish;
    this.pending = false;
  }

  request() {
    if (this.pending) return;
    this.pending = true;
    this.schedule(async () => {
      this.pending = false;
      await this.publish();
    });
  }
}

export function resultEnvelope(requestId, epoch, routeTicket, result) {
  return {
    type: "browser_handoff_result",
    request_id: requestId,
    route_ticket: routeTicket,
    receiver_epoch: epoch,
    status: result.status,
    opened_count: result.opened_count,
    total_count: result.total_count,
    entries: result.outcomes ?? [],
  };
}

// In-memory only: an extension restart deliberately loses this map and reports
// an unknown outcome instead of risking a duplicate browser-window creation.
export class RequestCoordinator {
  constructor({ open, now = () => performance.now(), deadlineMs = 10_000, maxActive = 8, maxResults = 256 }) {
    this.open = open;
    this.now = now;
    this.deadlineMs = deadlineMs;
    this.maxActive = maxActive;
    this.maxResults = maxResults;
    this.inFlight = new Map();
    this.results = new Map();
  }

  _key(epoch, requestId) {
    return `${epoch}:${requestId}`;
  }

  _remember(key, result) {
    this.results.delete(key);
    this.results.set(key, result);
    while (this.results.size > this.maxResults) this.results.delete(this.results.keys().next().value);
    return result;
  }

  async start(epoch, request) {
    const key = this._key(epoch, request.request_id);
    const cached = this.results.get(key);
    if (cached) return cached;
    const running = this.inFlight.get(key);
    if (running) {
      if (this.now() - running.startedAt > this.deadlineMs) {
        this.inFlight.delete(key);
        return this._remember(key, { status: "unknown", opened_count: 0, total_count: request.total_count ?? 0, outcomes: [{ reason: "operation_timeout" }] });
      }
      return { status: "pending", opened_count: 0, total_count: 0, outcomes: [] };
    }
    if (this.inFlight.size >= this.maxActive) {
      return { status: "failed", opened_count: 0, total_count: 0, outcomes: [{ reason: "too_many_active_requests" }] };
    }
    const startedAt = this.now();
    const operation = Promise.resolve()
      .then(() => this.open(request))
      .then((result) => this._remember(key, result))
      .catch(() => this._remember(key, { status: "unknown", opened_count: 0, total_count: request.total_count ?? 0, outcomes: [{ reason: "open_exception" }] }))
      .finally(() => this.inFlight.delete(key));
    this.inFlight.set(key, { startedAt, operation });
    return operation;
  }
}

export function installWorker(chrome, {
  epoch = crypto.randomUUID(), browser = "chrome", maxReconnects = null,
  setTimeoutFn = setTimeout,
} = {}) {
  // The native host binds this opaque ID to its browser parent process.  It is
  // never a profile identifier or authority for network routing.
  const browserInstanceId = epoch;
  let revision = 0;
  let port;
  const publishMetadata = async (requestId, targetPort = port) => {
    const queryRevision = revision;
    try {
      const windows = await chrome.windows.getAll({ populate: false });
      if (targetPort !== port) return;
      targetPort?.postMessage({
        type: "browser_handoff_metadata",
        epoch, browser_instance_id: browserInstanceId,
        coordinate_units: "browser_dip",
        revision: queryRevision,
        ...(requestId ? { request_id: requestId } : {}),
        windows: windows.map((window) => ({
          window_id: window.id, focused: window.focused === true,
          incognito: window.incognito === true, state: window.state,
          left: window.left, top: window.top, width: window.width, height: window.height,
        })),
      });
    } catch { /* correlation owns bounded refresh retries; other events coalesce */ }
  };
  const coalescer = new MetadataCoalescer({ publish: () => publishMetadata() });
  const bumpRevision = () => { revision += 1; coalescer.request(); };
  // Register every listener before connecting or awaiting any browser work.
  for (const event of [
    chrome.windows.onCreated, chrome.windows.onRemoved, chrome.windows.onFocusChanged,
    chrome.tabs.onCreated, chrome.tabs.onRemoved, chrome.tabs.onMoved, chrome.tabs.onAttached,
    chrome.tabs.onDetached, chrome.tabs.onReplaced, chrome.tabs.onUpdated,
  ]) event.addListener(bumpRevision);
  // Geometry is refreshed independently from tab-snapshot consistency. A
  // continuing native drag must not invalidate an otherwise stable tab list.
  // Native correlation checks current bounds and rejects resizing separately.
  chrome.windows.onBoundsChanged.addListener(() => coalescer.request());

  const coordinator = new RequestCoordinator({
    open: (request) => openDestinationWindow(chrome, request, { browser }),
  });
  let reconnects = 0;
  let connected = false;
  let lastResult = null;
  let receiverEpoch = null;
  if (chrome.runtime.onMessage?.addListener) {
    chrome.runtime.onMessage.addListener((message, _sender, respond) => {
      if (message?.type !== "browser_handoff_status") return undefined;
      Promise.resolve(chrome.extension?.isAllowedIncognitoAccess?.() ?? false)
        .then((incognitoAllowed) => respond({
          connected, browser_instance_id: browserInstanceId,
          incognito_allowed: incognitoAllowed === true,
          last_result: lastResult,
        }))
        .catch(() => respond({ connected, browser_instance_id: browserInstanceId, incognito_allowed: false, last_result: lastResult }));
      return true;
    });
  }
  const connect = () => {
    if (port) return;
    receiverEpoch = null;
    const connectedPort = chrome.runtime.connectNative("com.conduit.browser_handoff");
    port = connectedPort;
    connected = false;
    connectedPort.postMessage({
      type: "browser_handoff_hello", browser_instance_id: browserInstanceId,
    });
    connectedPort.onMessage.addListener(async (message) => {
      if (port !== connectedPort) return;
      if (message?.type === "browser_handoff_error" && message.reason === "bridge_disconnected") {
        connectedPort.disconnect();
        // Local disconnect does not fire onDisconnect on this end of a Port.
        handleDisconnect();
        return;
      }
      if (message?.type === "bridge_ready") {
        receiverEpoch = typeof message.bridge_epoch === "string" && message.bridge_epoch
          ? message.bridge_epoch : null;
        if (receiverEpoch) {
          connectedPort.postMessage({
            type: "browser_handoff_capabilities",
            browser_instance_id: browserInstanceId,
            receiver_epoch: receiverEpoch,
          });
          connected = true;
          reconnects = 0;
          bumpRevision();
        }
        return;
      }
      if (message?.type === "browser_handoff_metadata_request") {
        if (receiverEpoch && typeof message.request_id === "string" && message.request_id) {
          revision += 1;
          await publishMetadata(message.request_id, connectedPort);
        }
        return;
      }
      if (message?.type === "browser_handoff_close_window") {
        if (typeof message.window_id === "number") {
          try {
            await chrome.windows.remove(message.window_id);
          } catch { /* window may already be closed */ }
        }
        return;
      }
      if (message?.type === "browser_handoff_snapshot_request") {
        try {
          const snapshot = await captureWindow(chrome, message.window_id, { revision: () => revision });
          connectedPort.postMessage({ type: "browser_handoff_snapshot", request_id: message.request_id, epoch, snapshot });
        } catch {
          connectedPort.postMessage({ type: "browser_handoff_snapshot", request_id: message.request_id, epoch, status: "failed", reason: "snapshot_failed" });
        }
        return;
      }
      if (message?.type === "browser_handoff_request") {
        if (!receiverEpoch) {
          connectedPort.postMessage({ type: "browser_handoff_error", reason: "bridge_not_ready" });
          return;
        }
        try {
          const request = validateRequest(message.request);
          const result = await coordinator.start(epoch, request);
          lastResult = { status: result.status, opened_count: result.opened_count, total_count: result.total_count };
          connectedPort.postMessage(resultEnvelope(request.request_id, receiverEpoch, request.route_ticket, result));
        } catch {
          connectedPort.postMessage({ type: "browser_handoff_result", epoch, status: "failed", opened_count: 0, total_count: 0, outcomes: [{ reason: "invalid_request" }] });
        }
      }
    });
    function handleDisconnect() {
      void chrome.runtime.lastError;
      if (port !== connectedPort) return;
      connected = false;
      receiverEpoch = null;
      port = null;
      if (maxReconnects !== null && reconnects >= maxReconnects) return;
      const delay = Math.min(250 * (2 ** Math.min(reconnects, 6)), 3000);
      reconnects += 1;
      setTimeoutFn(connect, delay);
    }
    connectedPort.onDisconnect.addListener(handleDisconnect);
  };
  connect();
  return { epoch, coordinator, get port() { return port; } };
}

if (typeof chrome !== "undefined" && chrome.runtime?.connectNative) installWorker(chrome);

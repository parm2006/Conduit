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
  epoch = crypto.randomUUID(), browser = "chrome", maxReconnects = 3,
  setTimeoutFn = setTimeout,
} = {}) {
  // The native host binds this opaque ID to its browser parent process.  It is
  // never a profile identifier or authority for network routing.
  const browserInstanceId = epoch;
  let revision = 0;
  let port;
  const coalescer = new MetadataCoalescer({
    publish: async () => {
      try {
        const windows = await chrome.windows.getAll({ populate: false });
        port?.postMessage({
          type: "browser_handoff_metadata",
          epoch, browser_instance_id: browserInstanceId,
          coordinate_units: "browser_dip",
          revision,
          windows: windows.map((window) => ({
            window_id: window.id, focused: window.focused === true,
            incognito: window.incognito === true, state: window.state,
            left: window.left, top: window.top, width: window.width, height: window.height,
          })),
        });
      } catch { /* disconnects and API errors are retried only by later events */ }
    },
  });
  const bumpRevision = () => { revision += 1; coalescer.request(); };
  // Register every listener before connecting or awaiting any browser work.
  for (const event of [
    chrome.windows.onCreated, chrome.windows.onRemoved, chrome.windows.onFocusChanged, chrome.windows.onBoundsChanged,
    chrome.tabs.onCreated, chrome.tabs.onRemoved, chrome.tabs.onMoved, chrome.tabs.onAttached,
    chrome.tabs.onDetached, chrome.tabs.onReplaced, chrome.tabs.onUpdated,
  ]) event.addListener(bumpRevision);

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
    const connectedPort = chrome.runtime.connectNative("com.conduit.browser_handoff");
    port = connectedPort;
    connected = true;
    connectedPort.postMessage({
      type: "browser_handoff_hello", browser_instance_id: browserInstanceId,
    });
    connectedPort.onMessage.addListener(async (message) => {
      if (message?.type === "bridge_ready") {
        receiverEpoch = typeof message.bridge_epoch === "string" && message.bridge_epoch
          ? message.bridge_epoch : null;
        if (receiverEpoch) {
          connectedPort.postMessage({
            type: "browser_handoff_capabilities",
            browser_instance_id: browserInstanceId,
            receiver_epoch: receiverEpoch,
          });
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
    connectedPort.onDisconnect.addListener(() => {
      if (port === connectedPort) connected = false;
      if (reconnects >= maxReconnects) return;
      const delay = 250 * (2 ** reconnects);
      reconnects += 1;
      setTimeoutFn(connect, delay);
    });
  };
  connect();
  return { epoch, coordinator, get port() { return port; } };
}

if (typeof chrome !== "undefined" && chrome.runtime?.connectNative) installWorker(chrome);

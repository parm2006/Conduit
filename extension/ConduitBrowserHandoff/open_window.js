import { classifyUrl } from "./url_policy.js";

export async function openDestinationWindow(chrome, request, { browser, allowFileUrl = false } = {}) {
  if (request.incognito === true) {
    return {
      status: "failed", opened_count: 0, total_count: request.entries.length,
      outcomes: [{ reason: "incognito_unsupported" }],
    };
  }
  const planned = request.entries.map((entry) => ({ entry, decision: classifyUrl(entry.url, { browser, allowFileUrl }) }));
  const supported = planned.filter(({ decision }) => decision.allowed);
  const outcomes = planned
    .filter(({ decision }) => !decision.allowed)
    .map(({ entry, decision }) => ({ source_index: entry.source_index, reason: decision.reason }));
  if (supported.length === 0) {
    return { status: "failed", opened_count: 0, total_count: request.entries.length, outcomes };
  }

  let window;
  try {
    window = await chrome.windows.create({ url: "about:blank" });
  } catch {
    return { status: "failed", opened_count: 0, total_count: request.entries.length, outcomes: [{ reason: "window_create_failed" }] };
  }
  if (window.incognito === true) {
    return { status: "failed", opened_count: 0, total_count: request.entries.length, outcomes: [{ reason: "privacy_mismatch" }] };
  }

  const opened = [];
  for (const { entry, decision } of supported) {
    try {
      const tab = await chrome.tabs.create({ windowId: window.id, index: opened.length + 1, url: decision.url, active: false });
      opened.push({ entry, tab });
    } catch {
      outcomes.push({ source_index: entry.source_index, reason: "tab_create_failed" });
    }
  }
  if (opened.length === 0) {
    return { status: "failed", opened_count: 0, total_count: request.entries.length, outcomes };
  }

  const helperTabId = window.tabs?.[0]?.id;
  if (helperTabId !== undefined) await chrome.tabs.remove(helperTabId);
  const active = opened.find(({ entry }) => entry.active) ?? opened[0];
  await chrome.tabs.update(active.tab.id, { active: true });
  return {
    status: outcomes.length === 0 ? "complete" : "partial",
    opened_count: opened.length,
    total_count: request.entries.length,
    outcomes,
  };
}

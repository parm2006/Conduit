function snapshotFrom(window, revision) {
  const entries = [];
  let unreadable = false;
  for (const [source_index, tab] of (window.tabs ?? []).entries()) {
    if (typeof tab.url !== "string") {
      unreadable = true;
      continue;
    }
    entries.push({ source_index, url: tab.url, active: tab.active === true });
  }
  return {
    window_id: window.id,
    revision,
    incognito: window.incognito === true,
    total_count: (window.tabs ?? []).length,
    entries,
    unreadable,
  };
}

export async function captureWindow(chrome, windowId, { revision = () => 0 } = {}) {
  let latest;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const before = revision(windowId);
    latest = snapshotFrom(await chrome.windows.get(windowId, { populate: true }), before);
    const after = revision(windowId);
    latest.revision = after;
    if (before === after) {
      return { ...latest, complete_capture: !latest.unreadable };
    }
  }
  return { ...latest, complete_capture: false, reason: "changed_during_capture" };
}

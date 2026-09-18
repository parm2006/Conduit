const status = document.querySelector("#status");
const evidence = document.querySelector("#evidence");

function probeHeadline({ connection, correlation, diagnostics }) {
  if (connection !== "ready") {
    return `Probe host: ${connection}.`;
  }
  if (correlation) {
    return `Last result: ${correlation.status ?? "error"}`;
  }
  if (!diagnostics) {
    return "Connected, but diagnostic data has not arrived.";
  }
  const stage = diagnostics.host?.last_stage ?? "diagnostics_received";
  return `Connected. Last confirmed stage: ${stage}.`;
}

async function renderStatus() {
  try {
    const snapshot = await chrome.runtime.sendMessage({ type: "probe_status" });
    status.textContent = probeHeadline(snapshot);
    evidence.textContent = JSON.stringify(snapshot, null, 2);
  } catch {
    status.textContent = "Probe worker is unavailable.";
  }
}

void renderStatus();
setInterval(renderStatus, 500);

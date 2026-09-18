import { probeHeadline } from "./probe_status.js";

const status = document.querySelector("#status");
const evidence = document.querySelector("#evidence");

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

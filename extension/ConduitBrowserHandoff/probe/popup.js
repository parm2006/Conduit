const status = document.querySelector("#status");
const evidence = document.querySelector("#evidence");

chrome.runtime.sendMessage({ type: "probe_status" }).then(({ correlation }) => {
  if (!correlation) return;
  status.textContent = `Last result: ${correlation.status ?? "error"}`;
  evidence.textContent = JSON.stringify(correlation, null, 2);
}).catch(() => {
  status.textContent = "Probe worker is unavailable.";
});

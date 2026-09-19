const connection = document.querySelector("#connection");
const incognito = document.querySelector("#incognito");
const result = document.querySelector("#result");

function render(status) {
  connection.textContent = status.connected
    ? "Connected to local Conduit." : "Not connected — start Conduit and register the development host.";
  connection.className = status.connected ? "good" : "warn";
  incognito.textContent = "Private/incognito windows are not supported.";
  if (status.last_result) {
    result.textContent = `Last handoff: ${status.last_result.status} (${status.last_result.opened_count}/${status.last_result.total_count} tabs).`;
  } else {
    result.textContent = "No handoff has run in this browser session.";
  }
}

chrome.runtime.sendMessage({ type: "browser_handoff_status" }).then(render).catch(() => {
  render({ connected: false, incognito_allowed: false, last_result: null });
});

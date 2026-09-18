// Pure popup status formatting lives here so it can be tested without Chrome.

export function probeHeadline({ connection, correlation, diagnostics }) {
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

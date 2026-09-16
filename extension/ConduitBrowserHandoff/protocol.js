export const MAX_TABS_PER_WINDOW = 256;
export const MAX_URL_BYTES = 16 * 1024;
export const MAX_REQUEST_BYTES = 512 * 1024;

const requestFields = new Set(["protocol", "request_id", "route_ticket", "topology_version", "destination_machine_id", "incognito", "total_count", "entries", "complete_capture"]);
const entryFields = new Set(["source_index", "url", "active"]);
const resultFields = new Set(["request_id", "route_ticket", "status", "opened_count", "total_count", "entries", "receiver_epoch"]);
const resultEntryFields = new Set(["source_index", "reason"]);

function exactFields(value, expected) {
  return value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).length === expected.size && Object.keys(value).every((key) => expected.has(key));
}

function integer(value) { return typeof value === "number" && Number.isInteger(value); }

export function validateRequest(request) {
  if (!exactFields(request, requestFields) || request.protocol !== 1 || !/^[0-9a-f]{32}$/.test(request.request_id)
    || typeof request.route_ticket !== "string" || !request.route_ticket || request.route_ticket.length > 256
    || !integer(request.topology_version) || request.topology_version < 0
    || typeof request.destination_machine_id !== "string" || !request.destination_machine_id || request.destination_machine_id.length > 256
    || typeof request.incognito !== "boolean" || typeof request.complete_capture !== "boolean"
    || !integer(request.total_count) || request.total_count < 0 || request.total_count > MAX_TABS_PER_WINDOW
    || !Array.isArray(request.entries) || request.entries.length > request.total_count) throw new TypeError("invalid_request");
  const indexes = new Set();
  let active = 0;
  for (const entry of request.entries) {
    if (!exactFields(entry, entryFields) || !integer(entry.source_index) || entry.source_index < 0 || entry.source_index >= request.total_count
      || indexes.has(entry.source_index) || typeof entry.url !== "string" || new TextEncoder().encode(entry.url).length > MAX_URL_BYTES
      || typeof entry.active !== "boolean") throw new TypeError("invalid_entry");
    indexes.add(entry.source_index);
    active += entry.active;
  }
  if (active > 1 || (request.complete_capture && indexes.size !== request.total_count)) throw new TypeError("inconsistent_capture");
  if (new TextEncoder().encode(JSON.stringify(request)).length > MAX_REQUEST_BYTES) throw new TypeError("request_too_large");
  return request;
}

export function validateResult(result) {
  if (!exactFields(result, resultFields) || !/^[0-9a-f]{32}$/.test(result.request_id)
    || typeof result.route_ticket !== "string" || !result.route_ticket || result.route_ticket.length > 256
    || !["complete", "partial", "failed", "unknown"].includes(result.status)
    || !integer(result.opened_count) || !integer(result.total_count) || result.total_count < 0 || result.total_count > MAX_TABS_PER_WINDOW
    || result.opened_count < 0 || result.opened_count > result.total_count
    || typeof result.receiver_epoch !== "string" || !result.receiver_epoch || result.receiver_epoch.length > 256
    || !Array.isArray(result.entries) || result.entries.length > result.total_count) throw new TypeError("invalid_result");
  const indexes = new Set();
  for (const entry of result.entries) {
    if (!exactFields(entry, resultEntryFields) || !integer(entry.source_index) || entry.source_index < 0 || entry.source_index >= result.total_count
      || indexes.has(entry.source_index) || typeof entry.reason !== "string" || !entry.reason || entry.reason.length > 128) throw new TypeError("invalid_result_entry");
    indexes.add(entry.source_index);
  }
  if (result.status === "complete" && (result.opened_count !== result.total_count || result.entries.length !== 0)) throw new TypeError("inconsistent_complete_result");
  return result;
}

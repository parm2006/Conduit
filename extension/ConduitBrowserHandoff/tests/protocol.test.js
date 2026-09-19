import test from "node:test";
import assert from "node:assert/strict";

import { validateRequest, validateResult } from "../protocol.js";

const request = () => ({
  protocol: 1,
  request_id: "a".repeat(32),
  route_ticket: "ticket-1",
  topology_version: 4,
  destination_machine_id: "client-b",
  incognito: false,
  total_count: 2,
  entries: [
    { source_index: 0, url: "https://example.test/a", active: false },
    { source_index: 1, url: "https://example.test/a", active: true },
  ],
  complete_capture: true,
});

test("accepts duplicate URLs but rejects bool integers and duplicate indexes", () => {
  assert.equal(validateRequest(request()).entries.length, 2);
  assert.throws(() => validateRequest({ ...request(), total_count: true }));
  assert.throws(() => validateRequest({ ...request(), incognito: true }));
  assert.throws(() => validateRequest({ ...request(), entries: [
    { source_index: 0, url: "https://example.test/a", active: false },
    { source_index: 0, url: "https://example.test/a", active: true },
  ] }));
});

test("rejects an encoded request above the shared transport limit", () => {
  assert.throws(() => validateRequest({ ...request(), route_ticket: "x".repeat(513 * 1024) }));
});

test("matches Python identity-field bounds", () => {
  assert.throws(() => validateRequest({ ...request(), route_ticket: "x".repeat(257) }));
  assert.throws(() => validateRequest({ ...request(), destination_machine_id: "x".repeat(257) }));
});

test("accepts URL-free partial results and rejects a result URL", () => {
  const result = {
    request_id: "a".repeat(32), route_ticket: "ticket-1", status: "partial",
    opened_count: 1, total_count: 2,
    entries: [{ source_index: 1, reason: "blocked_scheme" }], receiver_epoch: "epoch-1",
  };
  assert.equal(validateResult(result).status, "partial");
  assert.throws(() => validateResult({ ...result, entries: [{ source_index: 1, reason: "blocked", url: "https://secret" }] }));
});

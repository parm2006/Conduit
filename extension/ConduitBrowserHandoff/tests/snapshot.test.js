import test from "node:test";
import assert from "node:assert/strict";

import { captureWindow } from "../snapshot.js";

test("captures source indexes and active state from one stable window read", async () => {
  const chrome = { windows: { async get() { return { incognito: false, tabs: [{ id: 1, url: "https://a", active: false }, { id: 2, url: "https://a", active: true }] }; } } };
  const result = await captureWindow(chrome, 7, { revision: () => 3 });

  assert.equal(result.complete_capture, true);
  assert.deepEqual(result.entries, [
    { source_index: 0, url: "https://a", active: false },
    { source_index: 1, url: "https://a", active: true },
  ]);
});

test("retries once then marks a changing tab list partial without mixing reads", async () => {
  let revision = 0;
  let reads = 0;
  const chrome = { windows: { async get() { reads += 1; revision += 1; return { incognito: false, tabs: [{ id: reads, url: `https://read-${reads}`, active: true }] }; } } };
  const result = await captureWindow(chrome, 7, { revision: () => revision });

  assert.equal(reads, 2);
  assert.equal(result.complete_capture, false);
  assert.equal(result.reason, "changed_during_capture");
  assert.deepEqual(result.entries, [{ source_index: 0, url: "https://read-2", active: true }]);
});

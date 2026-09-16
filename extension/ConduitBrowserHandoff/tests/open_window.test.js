import test from "node:test";
import assert from "node:assert/strict";

import { openDestinationWindow } from "../open_window.js";

test("opens supported duplicate URLs in one private window and activates the source active tab", async () => {
  const calls = [];
  const chrome = {
    windows: {
      async create(options) {
        calls.push(["window", options]);
        return { id: 5, incognito: true, tabs: [{ id: 50, url: "about:blank" }] };
      },
    },
    tabs: {
      async create(options) { calls.push(["tab", options]); return { id: 60 + options.index }; },
      async update(id, options) { calls.push(["update", id, options]); },
      async remove(id) { calls.push(["remove", id]); },
    },
  };

  const result = await openDestinationWindow(chrome, {
    incognito: true,
    entries: [
      { source_index: 0, url: "https://example.test/a", active: false },
      { source_index: 1, url: "https://example.test/a", active: true },
    ],
  }, { browser: "chrome" });

  assert.equal(result.status, "complete");
  assert.equal(result.opened_count, 2);
  assert.deepEqual(calls[0], ["window", { url: "about:blank", incognito: true }]);
  assert.deepEqual(calls.filter(([kind]) => kind === "tab").map(([, value]) => value.index), [1, 2]);
  assert.deepEqual(calls.at(-2), ["remove", 50]);
  assert.deepEqual(calls.at(-1), ["update", 62, { active: true }]);
});

test("does not remove the helper tab or retry when no entry can be opened", async () => {
  const removed = [];
  const chrome = {
    windows: { async create() { return { id: 5, incognito: false, tabs: [{ id: 50 }] }; } },
    tabs: {
      async create() { throw new Error("browser refused"); },
      async update() { throw new Error("must not activate"); },
      async remove(id) { removed.push(id); },
    },
  };

  const result = await openDestinationWindow(chrome, {
    incognito: false,
    entries: [{ source_index: 0, url: "https://example.test/a", active: true }],
  }, { browser: "chrome" });

  assert.equal(result.status, "failed");
  assert.deepEqual(removed, []);
});

test("fails closed when Chromium does not create the requested private window", async () => {
  let tabCreates = 0;
  const chrome = {
    windows: { async create() { return { id: 5, incognito: false, tabs: [{ id: 50 }] }; } },
    tabs: { async create() { tabCreates += 1; }, async update() {}, async remove() {} },
  };

  const result = await openDestinationWindow(chrome, {
    incognito: true,
    entries: [{ source_index: 0, url: "https://example.test/a", active: true }],
  }, { browser: "chrome" });

  assert.equal(result.status, "failed");
  assert.equal(result.outcomes[0].reason, "privacy_mismatch");
  assert.equal(tabCreates, 0);
});

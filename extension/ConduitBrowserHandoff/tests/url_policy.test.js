import test from "node:test";
import assert from "node:assert/strict";

import { classifyUrl } from "../url_policy.js";

test("allows ordinary web URLs and blank pages", () => {
  assert.deepEqual(classifyUrl("https://example.test/a", { browser: "chrome" }), { allowed: true, url: "https://example.test/a" });
  assert.deepEqual(classifyUrl("about:blank", { browser: "edge" }), { allowed: true, url: "about:blank" });
});

test("rejects executable, debug, extension, and crash schemes", () => {
  for (const url of ["javascript:alert(1)", "data:text/html,x", "blob:https://x/y", "devtools://devtools", "chrome-extension://abc/page.html", "chrome://crash/"]) {
    assert.equal(classifyUrl(url, { browser: "chrome" }).allowed, false, url);
  }
});

test("maps only explicitly supported internal pages to the target browser", () => {
  assert.deepEqual(classifyUrl("chrome://settings/", { browser: "edge" }), { allowed: true, url: "edge://settings/" });
  assert.equal(classifyUrl("chrome://flags/", { browser: "edge" }).allowed, false);
});

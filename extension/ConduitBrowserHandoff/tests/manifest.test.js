import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("shipping manifest has only the required MV3 permissions", async () => {
  const manifest = JSON.parse(await readFile(new URL("../manifest.json", import.meta.url)));

  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.name, "ConduitBrowserHandoff");
  assert.deepEqual(manifest.permissions.sort(), ["nativeMessaging", "tabs"]);
  assert.equal(manifest.incognito, "spanning");
  assert.equal(manifest.background.type, "module");
  assert.equal(manifest.content_scripts, undefined);
  assert.equal(manifest.host_permissions, undefined);
});

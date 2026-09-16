// Keep this table deliberately small. Entries require browser-specific manual
// verification before shipping support; arbitrary internal URLs are rejected.
const INTERNAL_PATHS = new Set(["newtab/", "settings/", "history/", "downloads/", "bookmarks/"]);
const BLOCKED_SCHEMES = new Set(["javascript:", "data:", "blob:", "devtools:"]);

function internalMapping(url, browser) {
  const match = /^(?:chrome|edge):\/\/([^/?#]+\/)$/.exec(url);
  if (!match || !INTERNAL_PATHS.has(match[1])) return null;
  return `${browser === "edge" ? "edge" : "chrome"}://${match[1]}`;
}

export function classifyUrl(url, { browser, allowFileUrl = false } = {}) {
  if (typeof url !== "string" || !url) return { allowed: false, reason: "invalid_url" };
  const mapped = internalMapping(url, browser);
  if (mapped) return { allowed: true, url: mapped };
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return { allowed: false, reason: "invalid_url" };
  }
  if (BLOCKED_SCHEMES.has(parsed.protocol) || parsed.protocol === "chrome-extension:" || parsed.protocol === "edge-extension:") {
    return { allowed: false, reason: "blocked_scheme" };
  }
  if (parsed.protocol === "file:") {
    return allowFileUrl ? { allowed: true, url } : { allowed: false, reason: "file_permission_required" };
  }
  if (["http:", "https:", "about:"].includes(parsed.protocol) && (parsed.protocol !== "about:" || url === "about:blank")) {
    return { allowed: true, url };
  }
  return { allowed: false, reason: "unsupported_url" };
}

/**
 * Hosts allowed to serve a download over plain `http:`.
 *
 * Local development runs the API on `http://localhost:8000` (the Vite dev
 * server proxies `/api` to it), so loopback is the one legitimate plain-http
 * case. Everything else must be `https:` — an API-supplied URL is handed
 * straight to the browser, so a compromised or misconfigured response must not
 * be able to navigate the tab to `javascript:`, `data:` or a plain-http host.
 */
function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}

/**
 * Whether an absolute, API-supplied URL may be given to the browser as-is.
 *
 * `https:` is accepted; `http:` only for loopback hosts (local dev). Relative
 * paths and unknown schemes are rejected — those are served through the
 * authenticated streaming endpoints instead.
 */
export function isTrustedDownloadUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:") return true;
    if (parsed.protocol === "http:") return isLoopbackHost(parsed.hostname);
    return false;
  } catch {
    return false;
  }
}

/**
 * Trigger a browser download from a remote URL (no auth needed — presigned).
 *
 * Returns `false` (without navigating) when the URL's scheme is not trusted, so
 * callers can fall back to the authenticated streaming path instead of
 * silently doing nothing.
 */
export function downloadFromUrl(url: string, filename?: string): boolean {
  if (!isTrustedDownloadUrl(url)) return false;
  // Use an anchor with `download` so the filename is respected; fall back to
  // opening the URL if the browser blocks the `download` attribute cross-origin.
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  return true;
}

/** Save an in-memory Blob as a file (used for auth-streamed downloads). */
export function saveBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Release the object URL shortly after the download starts.
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

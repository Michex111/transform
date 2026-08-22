/** Trigger a browser download from a remote URL (no auth needed — presigned). */
export function downloadFromUrl(url: string, filename?: string): void {
  // Use an anchor with `download` so the filename is respected; fall back to
  // opening the URL if the browser blocks the `download` attribute cross-origin.
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
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

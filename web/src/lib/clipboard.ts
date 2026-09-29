// Copy-to-clipboard, with a fallback for insecure contexts.
//
// `navigator.clipboard` only exists on a secure origin, and this app is
// reachable over plain http in development and on some self-hosted setups, so a
// copy control that only used the async API would silently do nothing there.
// The environment is injectable so the decision logic (prefer the modern API,
// fall back on absence or failure, never throw) is testable in Node.

export interface ClipboardEnv {
  /** The modern, promise-based clipboard write, when the browser exposes it. */
  writeText?: (text: string) => Promise<unknown>;
  /** The legacy `document.execCommand("copy")` path; returns whether it worked. */
  execCommandCopy?: (text: string) => boolean;
}

/**
 * The real browser environment.
 *
 * Every access is guarded: `navigator`/`document` are absent under server
 * rendering and in the Node test environment, and touching them there would
 * throw instead of degrading.
 */
export function browserClipboardEnv(): ClipboardEnv {
  const env: ClipboardEnv = {};
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    env.writeText = (text) => navigator.clipboard.writeText(text);
  }
  if (typeof document !== "undefined" && typeof document.execCommand === "function") {
    env.execCommandCopy = (text) => {
      const area = document.createElement("textarea");
      area.value = text;
      // Keep it off-screen but still focusable/selectable, and stop iOS from
      // scrolling to it when it receives focus.
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.top = "-1000px";
      area.style.opacity = "0";
      document.body.appendChild(area);
      try {
        area.focus();
        area.select();
        return document.execCommand("copy");
      } catch {
        return false;
      } finally {
        document.body.removeChild(area);
      }
    };
  }
  return env;
}

/**
 * Copy `text`, resolving `true` only when a method actually reported success.
 *
 * Never rejects: a denied clipboard permission is a normal outcome, and the
 * caller shows a confirmation only on `true` rather than throwing into the UI.
 */
export async function copyText(
  text: string,
  env: ClipboardEnv = browserClipboardEnv(),
): Promise<boolean> {
  if (!text) return false;

  if (env.writeText) {
    try {
      await env.writeText(text);
      return true;
    } catch {
      // Permission denied or an insecure context despite the API existing —
      // fall through to the legacy path rather than giving up.
    }
  }

  if (env.execCommandCopy) {
    try {
      return env.execCommandCopy(text);
    } catch {
      return false;
    }
  }

  return false;
}

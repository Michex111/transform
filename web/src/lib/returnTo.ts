/**
 * Where to send a user once they have signed in.
 *
 * Every guard that needs a session records the page the user was trying to
 * reach so sign-in can return them to it. That destination is attacker-visible
 * input: it is whatever URL a third party caused the browser to open. Two
 * flows depend on it and both are reached from *outside* the app:
 *
 * * the MCP consent screen — an AI application opens a cold browser tab at
 *   `/app/authorize?...`, and a signed-out visitor must land back on that exact
 *   request after signing in, or the connection silently never completes;
 * * Stripe's return to `/app/billing?credits=success`, where dropping the query
 *   string hides the payment confirmation.
 *
 * So the value is used to navigate, which is why it is validated here rather
 * than trusted. Routing to an absolute URL is the risk: the browser would leave
 * the app entirely, and a crafted `from` turns our own sign-in into a hop to a
 * page that can imitate it.
 */

/**
 * The SPA route that renders the OAuth consent screen.
 *
 * Mirrors the backend's `MCP_CONSENT_PATH` (`src/infrastructure/config/settings.py`,
 * default `/app/authorize`). The API builds the browser redirect from its own
 * copy, so the two must agree; `returnTo.test.ts` pins the literal so a rename
 * on either side fails a test instead of stranding harnesses on a 404.
 */
export const MCP_AUTHORIZE_ROUTE = "/app/authorize";

/** Where a signed-in user goes when no destination was recorded. */
export const DEFAULT_RETURN_PATH = "/app/dashboard";

/**
 * Coerce a recorded destination into a path that is safe to navigate to.
 *
 * Accepts only an in-app absolute path, so the result can never leave the
 * origin. Anything else — a missing value, a non-string from
 * `history.state` that survived a deploy, an absolute URL, a
 * protocol-relative URL, or a backslash form a browser normalises into one —
 * falls back to the dashboard rather than being repaired. There is no useful
 * "closest valid" reading of a hostile URL, and guessing would reintroduce the
 * redirect this function exists to prevent.
 */
export function safeReturnPath(from: unknown, fallback: string = DEFAULT_RETURN_PATH): string {
  if (typeof from !== "string" || from.length === 0) return fallback;
  // An in-app path always starts with a single forward slash.
  if (!from.startsWith("/")) return fallback;
  // `//evil.example` is protocol-relative: the browser reads it as a full URL
  // to another origin, so it is not an in-app path despite the leading slash.
  if (from.startsWith("//")) return fallback;
  // Browsers normalise `\` to `/` in a special-scheme URL's authority, so
  // `/\evil.example` behaves like `//evil.example`. `\\` is covered by the same
  // normalisation, and `\/` by the check above (`\/` does not start with `/`).
  if (from.startsWith("/\\")) return fallback;
  return from;
}

/**
 * Whether a path is an OAuth consent request from an AI application.
 *
 * Used to explain *why* a visitor is being asked to sign in. Someone who
 * clicked "Connect" in an agent and then landed on a bare sign-in form has no
 * way to tell whether they are in the right place, and the usual result is that
 * they abandon the connection rather than complete it.
 */
export function isAuthorizationRequest(path: string): boolean {
  return path.split("?")[0] === MCP_AUTHORIZE_ROUTE;
}

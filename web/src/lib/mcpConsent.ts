/**
 * Pure rules for the OAuth consent handoff.
 *
 * The consent screen decides *whether* an application may act on a user's
 * account; this module holds the parts of finishing that decision that have a
 * right answer independent of the markup — so they can be tested directly
 * rather than through a renderer, which this project does not have (no jsdom;
 * see `pages/public/RegisterPage.test.tsx`).
 */

/** Whether the visitor allowed or refused the request. */
export type ConsentOutcomeKind = "approved" | "denied";

/** A completed decision, held until the visitor hands the result back. */
export interface ConsentOutcome {
  kind: ConsentOutcomeKind;
  /** The application's own display name, as it registered itself. */
  clientName: string;
  /** The application's callback. Already validated server-side. */
  redirectUrl: string;
}

/**
 * The redirect that tells the application the visitor refused.
 *
 * `error=access_denied` is the standard refusal, and returning it is what lets
 * an agent report "access denied" instead of hanging until its own request
 * times out waiting for a code that will never arrive.
 *
 * Returns `null` when the redirect URI cannot be parsed. That is not a
 * theoretical branch: the value arrives from the client's registration, and the
 * caller's job is to stop and send the visitor somewhere useful rather than
 * navigate to something unusable.
 */
export function denialUrl(redirectUri: string, state: string | null): string | null {
  try {
    const target = new URL(redirectUri);
    target.searchParams.set("error", "access_denied");
    if (state) target.searchParams.set("state", state);
    return target.toString();
  } catch {
    return null;
  }
}

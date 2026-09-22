import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { motion } from "motion/react";
import { CheckCircle, EnvelopeSimple, SpinnerGap, WarningCircle } from "@phosphor-icons/react";
import { api } from "@/api/client";
import {
  createSingleFlightVerifier,
  verificationStatusFor,
  type VerificationStatus,
} from "@/lib/emailVerification";
import { Button, Logo } from "@/components/ui";

/**
 * Single-flight for the whole page module.
 *
 * Module scope, not a ref or `useMemo`: React 18 StrictMode remounts the
 * component in development, so anything held in component state is recreated and
 * the guard would be defeated — issuing a second request for an already-spent
 * single-use token. See `lib/emailVerification.ts` for the full rationale.
 */
const verifyTokenOnce = createSingleFlightVerifier((token) => api.verifyEmail(token));

/**
 * Landing page for the link in the verification email.
 *
 * The token arrives in the query string, so this page is reachable by hard load
 * (a fresh browser, a mail client's in-app viewer). It therefore needs a route
 * stub in `spa-route-stubs.ts` like the other public routes.
 */
export function VerifyEmailPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [status, setStatus] = useState<VerificationStatus>(token ? "verifying" : "missing");
  const [username, setUsername] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) return;

    // A plain per-run flag is correct here. StrictMode's second run gets a
    // fresh `cancelled === false`, and because the request itself is
    // single-flighted above, that second run observes the *same* pending
    // promise — so its result is applied instead of being discarded.
    let cancelled = false;

    verifyTokenOnce(token)
      .then((result) => {
        if (cancelled) return;
        setUsername(result.username);
        setMessage(result.message);
        setStatus(verificationStatusFor(result));
        // Strip the token from the address bar and history. It is single-use and
        // now spent, but leaving a credential in the URL (and in a shared
        // screenshot or synced history) is needless exposure.
        window.history.replaceState({}, "", "/verify-email");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setMessage(err instanceof Error ? err.message : "Verification failed.");
        setStatus("invalid");
      });

    return () => {
      cancelled = true;
    };
  }, [token]);

  function goToSignIn() {
    // Hand the username over so the sign-in form is pre-filled: the user just
    // proved they own the address, and typing it again is pure friction.
    navigate("/login", { replace: true, state: username ? { username } : undefined });
  }

  return (
    <div className="format-glyph-field flex min-h-[70vh] items-center justify-center px-4 py-12">
      <motion.div
        className="w-full max-w-sm rounded-2xl border border-outline bg-surface p-8 text-center"
        initial={{ opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ type: "spring", stiffness: 220, damping: 22 }}
      >
        <div className="mb-6 flex justify-center">
          <Logo />
        </div>

        {status === "verifying" && (
          <>
            <SpinnerGap
              size={34}
              weight="bold"
              className="mx-auto animate-spin text-primary"
              aria-hidden="true"
            />
            <h1 className="mt-5 font-display text-2xl font-semibold">Verifying your email…</h1>
            <p className="mt-2 text-sm text-muted">This will only take a moment.</p>
          </>
        )}

        {(status === "verified" || status === "already") && (
          <>
            <motion.div
              className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-outline bg-surface-variant"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 260, damping: 18 }}
            >
              <CheckCircle size={26} weight="duotone" className="text-success" aria-hidden="true" />
            </motion.div>
            <h1 className="mt-5 font-display text-2xl font-semibold">
              {status === "already" ? "Already verified" : "Email verified"}
            </h1>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              {message || "Your email address is verified. You can now sign in."}
            </p>
            <Button className="mt-6 w-full" onClick={goToSignIn}>
              Continue to sign in
            </Button>
          </>
        )}

        {status === "invalid" && (
          <>
            <motion.div
              className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-outline bg-surface-variant"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 260, damping: 18 }}
            >
              <WarningCircle size={26} weight="duotone" className="text-warning" aria-hidden="true" />
            </motion.div>
            <h1 className="mt-5 font-display text-2xl font-semibold">Link not valid</h1>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              {message || "This verification link is invalid or has expired."}
            </p>
            {/* The most common cause of "invalid" is a double click: the first
                click verified the account and consumed the token. Say so, so a
                user who is actually already verified does not conclude their
                account is broken. */}
            <p className="mt-3 text-sm leading-relaxed text-muted">
              If you have already clicked this link once, your address is verified — just sign in.
              Otherwise, sign in and request a new link from the sign-in screen.
            </p>
            <Button className="mt-6 w-full" onClick={goToSignIn}>
              Go to sign in
            </Button>
          </>
        )}

        {status === "missing" && (
          <>
            <motion.div
              className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-outline bg-surface-variant"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 260, damping: 18 }}
            >
              <EnvelopeSimple size={26} weight="duotone" className="text-primary" aria-hidden="true" />
            </motion.div>
            <h1 className="mt-5 font-display text-2xl font-semibold">Check your email</h1>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              This page activates your account from the link we emailed you. It looks like no link was
              included — open the link from your inbox, or request a new one from the sign-in screen.
            </p>
            <Button className="mt-6 w-full" onClick={goToSignIn}>
              Go to sign in
            </Button>
          </>
        )}

        <p className="mt-6 text-center text-sm">
          <Link to="/" className="text-muted hover:text-on-background">
            Back to home
          </Link>
        </p>
      </motion.div>
    </div>
  );
}

import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import { EnvelopeSimple, Key } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useToast } from "@/auth/ToastContext";
import { Button, Field, Logo } from "@/components/ui";

/**
 * What the confirmation panel shows when the server's message is unusable.
 *
 * The API's own copy is preferred (it is the contract's wording), but a 202 with
 * an empty body must still say something honest — hence "if an account exists"
 * rather than "we sent you a link".
 */
const FALLBACK_CONFIRMATION =
  "If an account with that email address exists, we've sent instructions for resetting your password.";

/** The address the request was accepted for, plus the server's confirmation. */
interface SentState {
  email: string;
  message: string;
}

interface ForgotPasswordViewProps {
  email: string;
  onEmailChange: (value: string) => void;
  onSubmit: (e: FormEvent) => void;
  busy: boolean;
  /** Non-null once the request was accepted; replaces the form with the panel. */
  sent: SentState | null;
  /** Return to the form so the address can be re-submitted (or corrected). */
  onTryAgain: () => void;
  onBackToSignIn: () => void;
}

/**
 * Ask for a password-reset link.
 *
 * Frontend-only in the sense that matters: the API answers 202 for *any*
 * address, so this page can never learn whether the address exists. The
 * confirmation is therefore worded conditionally and is shown identically to a
 * user with an account and one without.
 */
export function ForgotPasswordView({
  email,
  onEmailChange,
  onSubmit,
  busy,
  sent,
  onTryAgain,
  onBackToSignIn,
}: ForgotPasswordViewProps) {
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

        {sent ? (
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
              {sent.message || FALLBACK_CONFIRMATION}
            </p>
            <p className="mt-1 break-all font-mono text-sm text-on-background">{sent.email}</p>

            <p className="mt-4 text-sm leading-relaxed text-muted">
              It can take a minute or two to arrive. Open the link to choose a new password — the
              link expires 60 minutes after it was sent.
            </p>

            <div className="mt-6 space-y-3">
              <Button className="w-full" onClick={onBackToSignIn}>
                Back to sign in
              </Button>
              <Button variant="secondary" className="w-full" onClick={onTryAgain}>
                Try again
              </Button>
            </div>

            {/* Spam folders are the most common reason a user cannot find the
                mail — saying so here prevents a support round-trip. */}
            <p className="mt-6 text-xs leading-relaxed text-muted">
              Do not see it? Check your spam or junk folder first. Re-sending too quickly is
              rate-limited, so give it a minute before trying again.
            </p>
          </>
        ) : (
          <>
            <motion.div
              className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-outline bg-surface-variant"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 260, damping: 18 }}
            >
              <Key size={26} weight="duotone" className="text-primary" aria-hidden="true" />
            </motion.div>

            <h1 className="mt-5 font-display text-2xl font-semibold">Reset your password</h1>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Enter your email address and we'll send you a link to choose a new password.
            </p>

            {/* `text-left` because the card centres its text; a centred field
                label reads like part of the sentence above it. */}
            <form onSubmit={onSubmit} className="mt-6 space-y-4 text-left">
              <Field
                label="Email"
                type="email"
                value={email}
                onChange={(e) => onEmailChange(e.target.value)}
                autoComplete="email"
                hint="If the address has an account, the link goes there."
                disabled={busy}
                required
              />
              <Button type="submit" className="w-full" disabled={busy}>
                {busy ? "Sending…" : "Send reset link"}
              </Button>
            </form>

            <p className="mt-4 text-xs leading-relaxed text-muted">
              The link is single-use and expires 60 minutes after it's sent.
            </p>
          </>
        )}

        {!sent && (
          <p className="mt-6 text-center text-sm">
            <Link to="/login" className="text-muted hover:text-on-background">
              Back to sign in
            </Link>
          </p>
        )}
      </motion.div>
    </div>
  );
}

/**
 * Route component: owns the request, the view stays a pure function of its
 * props (which is what makes every branch render-testable — the repo has no
 * DOM, so `renderToString` is the only renderer available).
 */
export function ForgotPasswordPage() {
  const navigate = useNavigate();
  const { error } = useToast();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<SentState | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;

    setBusy(true);
    try {
      const result = await api.forgotPassword(email);
      // 202 is returned for every address, so this is a confirmation that the
      // request was *accepted*, not that a mail was delivered. The panel's copy
      // says the same thing for that reason.
      setSent({ email, message: result.message });
    } catch (err) {
      // A transport failure is genuinely different from "no such account" (which
      // is a 202), and the only honest thing to do is say the request did not go
      // through. The form stays usable.
      error(
        err instanceof Error ? err.message : "Could not send the reset link. Try again in a moment.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <ForgotPasswordView
      email={email}
      onEmailChange={setEmail}
      onSubmit={onSubmit}
      busy={busy}
      sent={sent}
      onTryAgain={() => setSent(null)}
      onBackToSignIn={() => navigate("/login")}
    />
  );
}

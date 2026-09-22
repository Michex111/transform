import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { ArrowClockwise, EnvelopeSimple } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button } from "@/components/ui";

/**
 * Client-side mirror of the API's `EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS`.
 *
 * The server silently suppresses a resend inside the cooldown (it must: telling
 * the caller "too soon" would leak that the address exists). Without a matching
 * client-side countdown the user clicks, sees "sent", and receives nothing —
 * which reads as a broken button. Keep this in step with the setting.
 */
const RESEND_COOLDOWN_SECONDS = 60;

interface VerificationNoticeProps {
  /** Address the link was (or would be) sent to. */
  email: string;
  heading?: string;
  intro?: string;
  /** Where "back" goes — sign-in for both current callers. */
  onBack: () => void;
  backLabel?: string;
}

/**
 * "Check your email" panel, shared by the two places a user can end up needing
 * a verification link:
 *
 *  - right after registering (the account exists but cannot sign in yet), and
 *  - when sign-in is refused with `EMAIL_NOT_VERIFIED`.
 *
 * Both need the same three things — what happened, where to look, and a way to
 * get another link — so they share one implementation rather than drifting
 * apart.
 */
export function VerificationNotice({
  email,
  heading = "Check your email",
  intro,
  onBack,
  backLabel = "Back to sign in",
}: VerificationNoticeProps) {
  const { api } = useAuth();
  const { success, error } = useToast();
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setInterval(() => setCooldown((s) => Math.max(0, s - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [cooldown]);

  async function onResend() {
    setBusy(true);
    try {
      const result = await api.resendVerification(email);
      // Start the countdown only after the server accepted the request, so a
      // failed call does not lock the button for a minute.
      setCooldown(RESEND_COOLDOWN_SECONDS);
      success(result.message || "If an account with that address needs verification, a new link is on its way.");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not send the email. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.div
      className="w-full max-w-sm rounded-2xl border border-outline bg-surface p-8 text-center"
      initial={{ opacity: 0, y: 16, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 220, damping: 22 }}
    >
      <motion.div
        className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-outline bg-surface-variant"
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 260, damping: 18, delay: 0.05 }}
      >
        <EnvelopeSimple size={26} weight="duotone" className="text-primary" aria-hidden="true" />
      </motion.div>

      <h1 className="mt-5 font-display text-2xl font-semibold">{heading}</h1>

      <p className="mt-3 text-sm leading-relaxed text-muted">
        {intro ?? "We sent a verification link to"}
      </p>
      <p className="mt-1 break-all font-mono text-sm text-on-background">{email}</p>

      <p className="mt-4 text-sm leading-relaxed text-muted">
        Open that link to activate your account. The link is valid for 24 hours, and you cannot
        sign in until your address is verified.
      </p>

      <div className="mt-6 space-y-3">
        <Button className="w-full" onClick={onResend} disabled={busy || cooldown > 0}>
          <ArrowClockwise size={16} weight="bold" aria-hidden="true" />
          {busy
            ? "Sending…"
            : cooldown > 0
              ? `Resend in ${cooldown}s`
              : "Resend verification email"}
        </Button>
        <Button variant="secondary" className="w-full" onClick={onBack}>
          {backLabel}
        </Button>
      </div>

      {/* Spam folders are the most common reason a user cannot find the mail —
          saying so here prevents a support round-trip. */}
      <p className="mt-6 text-xs leading-relaxed text-muted">
        Do not see it? Check your spam or junk folder before requesting another link.
      </p>
    </motion.div>
  );
}

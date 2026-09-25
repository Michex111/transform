import { useState, type ChangeEvent, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { motion } from "motion/react";
import { CheckCircle, Key, WarningCircle } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useToast } from "@/auth/ToastContext";
import type { ResetPasswordResponse } from "@/api/types";
import { Button, Field, Logo } from "@/components/ui";
import {
  MIN_PASSWORD_LENGTH,
  PASSWORD_RESET_INVALID_MESSAGE,
  passwordResetFailureMessage,
  passwordResetLinkState,
  validateNewPassword,
  type NewPasswordErrors,
} from "@/lib/passwordReset";

/** Which panel the page shows. Derived from the token and the request outcome. */
type ResetPasswordPhase = "missing" | "form" | "dead" | "done";

/** A password input with its own error line, so the message sits under the field. */
function PasswordField({
  id,
  label,
  hint,
  autoComplete,
  value,
  onChange,
  error,
}: {
  id: string;
  label: string;
  hint?: string;
  autoComplete: string;
  value: string;
  onChange: (e: ChangeEvent<HTMLInputElement>) => void;
  error?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Field
        id={id}
        label={label}
        hint={hint}
        type="password"
        autoComplete={autoComplete}
        value={value}
        onChange={onChange}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-error` : undefined}
      />
      {error && (
        // `role="alert"`, unlike `PasswordSection`'s version of this markup.
        // There the accompanying toast is the live announcement; here nothing
        // else speaks on the client-side validation path (no request is made,
        // so no toast fires), and a screen-reader user would otherwise be told
        // nothing at all when the submit is refused.
        <p id={`${id}-error`} role="alert" className="text-xs text-error">
          {error}
        </p>
      )}
    </div>
  );
}

interface ResetPasswordViewProps {
  phase: ResetPasswordPhase;
  password: string;
  confirm: string;
  onPasswordChange: (value: string) => void;
  onConfirmChange: (value: string) => void;
  /** Per-field messages from `validateNewPassword`; empty until the first submit. */
  errors: NewPasswordErrors;
  busy: boolean;
  onSubmit: (e: FormEvent) => void;
  /** The API's confirmation copy, shown on the success panel. */
  message: string;
  /** "Continue to sign in", pre-filling the username the reset proved. */
  onContinue: () => void;
  /** Send the user to the request form for the missing/dead-token panels. */
  onRequestNewLink: () => void;
}

/**
 * Landing page for the link in the password-reset email.
 *
 * The token arrives in the query string, so this page is reached by a hard load
 * (a mail client's in-app viewer, a fresh browser) and needs a route stub in
 * `spa-route-stubs.ts` like the other public routes.
 *
 * The token is consumed on SUBMIT, never on load, so there is no mount-time
 * request to single-flight. (That is deliberate: the combination of a "run once"
 * ref with a "cancel on unmount" flag, documented in `lib/emailVerification.ts`,
 * silently discards the resolved response. Nothing here can hit it.)
 *
 * The markup is a pure function of its props rather than reading state directly:
 * the repo's test environment is `node` (no jsdom, no testing-library), so
 * `renderToString` is the only renderer, and it cannot fire a submit — the
 * branches above could not be pinned any other way.
 */
export function ResetPasswordView({
  phase,
  password,
  confirm,
  onPasswordChange,
  onConfirmChange,
  errors,
  busy,
  onSubmit,
  message,
  onContinue,
  onRequestNewLink,
}: ResetPasswordViewProps) {
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

        {phase === "missing" && (
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
              This page sets a new password from the link in your email — and no reset token was
              included in this address.
            </p>
            {/* A real `<button>` navigated by router state rather than a
                `Link` wrapping a `Button`: an anchor may not contain an
                interactive element, and the nested form gave one action two
                tab stops. */}
            <Button type="button" className="mt-6 w-full" onClick={onRequestNewLink}>
              Request a new link
            </Button>
            <p className="mt-6 text-center text-sm">
              <Link to="/login" className="text-muted hover:text-on-background">
                Back to sign in
              </Link>
            </p>
          </>
        )}

        {phase === "dead" && (
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
            {/* The server's own words: it cannot say whether the token was
                unknown, already used, or expired without disclosing whether one
                ever existed, so the page repeats the single generic line. */}
            <p className="mt-3 text-sm leading-relaxed text-muted">
              {PASSWORD_RESET_INVALID_MESSAGE}
            </p>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Reset links are single-use and expire 60 minutes after they are sent, so this one
              cannot be used again — request a new link and it will work.
            </p>
            <Button type="button" className="mt-6 w-full" onClick={onRequestNewLink}>
              Request a new link
            </Button>
            <p className="mt-6 text-center text-sm">
              <Link to="/login" className="text-muted hover:text-on-background">
                Back to sign in
              </Link>
            </p>
          </>
        )}

        {phase === "done" && (
          <>
            <motion.div
              className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-outline bg-surface-variant"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 260, damping: 18 }}
            >
              <CheckCircle size={26} weight="duotone" className="text-success" aria-hidden="true" />
            </motion.div>
            <h1 className="mt-5 font-display text-2xl font-semibold">Password updated</h1>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              {message || "Your password has been updated. Sign in with your new password."}
            </p>
            <Button type="button" className="mt-6 w-full" onClick={onContinue}>
              Continue to sign in
            </Button>
            {/* Honest about what this does not do: the tokens are stateless JWTs
                with no server-side session store, so a reset cannot revoke
                anything. Implying a global sign-out would be a lie. */}
            <p className="mt-4 text-xs leading-relaxed text-muted">
              This does not sign you out anywhere else: other devices keep working until their
              access token expires — up to 30 minutes. If you think someone else has your account,
              sign out of those devices individually.
            </p>
          </>
        )}

        {phase === "form" && (
          <>
            <motion.div
              className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border border-outline bg-surface-variant"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ type: "spring", stiffness: 260, damping: 18 }}
            >
              <Key size={26} weight="duotone" className="text-primary" aria-hidden="true" />
            </motion.div>
            <h1 className="mt-5 font-display text-2xl font-semibold">Choose a new password</h1>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              Set a new password for your account, then sign in with it.
            </p>

            <form onSubmit={onSubmit} className="mt-6 space-y-4 text-left">
              <PasswordField
                id="new-password"
                label="New password"
                hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
                autoComplete="new-password"
                value={password}
                onChange={(e) => onPasswordChange(e.target.value)}
                error={errors.password}
              />
              <PasswordField
                id="confirm-password"
                label="Confirm new password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => onConfirmChange(e.target.value)}
                error={errors.confirm}
              />
              <Button type="submit" className="w-full" disabled={busy}>
                {busy ? "Updating…" : "Update password"}
              </Button>
            </form>
          </>
        )}
      </motion.div>
    </div>
  );
}

/**
 * Route component: owns the submit. No effect — the token is spent on submit,
 * so a StrictMode double render would have nothing to duplicate.
 */
export function ResetPasswordPage() {
  const navigate = useNavigate();
  const { error } = useToast();
  const [params] = useSearchParams();
  const token = params.get("token");

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<NewPasswordErrors>({});
  const [busy, setBusy] = useState(false);
  const [dead, setDead] = useState(false);
  const [result, setResult] = useState<ResetPasswordResponse | null>(null);

  const phase: ResetPasswordPhase = result
    ? "done"
    : dead
      ? "dead"
      : passwordResetLinkState(token) === "missing"
        ? "missing"
        : "form";

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (busy || !token) return;

    const found = validateNewPassword(password, confirm);
    setErrors(found);
    if (found.password || found.confirm) return;

    setBusy(true);
    try {
      const res = await api.resetPassword({ token, new_password: password });
      setResult(res);
      // The token is single-use and now spent. Strip it from the address bar and
      // history: a credential left in a shared screenshot or a synced history
      // entry is needless exposure. Same move as VerifyEmailPage.
      window.history.replaceState({}, "", "/reset-password");
    } catch (err) {
      const failure = passwordResetFailureMessage(err);
      // A 400 means the token is dead — unknown, already used, or expired. The
      // server cannot say which, and retrying the same token can never succeed,
      // so show the recovery panel instead of a password box doomed to fail.
      if (failure === PASSWORD_RESET_INVALID_MESSAGE) {
        setDead(true);
        // Same hygiene as the success path: the token is spent, so it must not
        // linger in the address bar, a screenshot or a synced history entry.
        // Deliberately only on THIS branch — a transient network failure leaves
        // a usable token in the URL, which is what makes a reload retryable.
        window.history.replaceState({}, "", "/reset-password");
      } else {
        error(failure);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <ResetPasswordView
      phase={phase}
      password={password}
      confirm={confirm}
      onPasswordChange={setPassword}
      onConfirmChange={setConfirm}
      errors={errors}
      busy={busy}
      onSubmit={onSubmit}
      message={result?.message ?? ""}
      onContinue={() =>
        // Hand the username over so the sign-in form is pre-filled: the user
        // just proved they own the account, and typing it again is pure
        // friction. `LoginPage` reads `location.state.username`.
        navigate("/login", {
          replace: true,
          state: result?.username ? { username: result.username } : undefined,
        })
      }
      onRequestNewLink={() => navigate("/forgot-password")}
    />
  );
}

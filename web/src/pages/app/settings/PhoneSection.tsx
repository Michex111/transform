import { useEffect, useRef, useState, type FormEvent } from "react";
import { Phone } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Badge, Button, Card, Field } from "@/components/ui";
import { Modal } from "@/components/Modal";
import {
  INVALID_PHONE_NUMBER_MESSAGE,
  attemptsExhausted,
  formatPhoneDisplay,
  isE164,
  normalizePhoneInput,
  phoneErrorFor,
} from "@/lib/phone";
import { PHONE_CODE_LENGTH, isCompleteCode, sanitizeCodeInput } from "@/lib/verificationCode";
import type { PhoneVerificationStatusResponse } from "@/api/types";

/** One message element, referenced by whichever input the failure belongs to. */
const ERROR_ID = "phone-error";

/** Which input a failure came from, so only that field is marked invalid. */
type Failure = { field: "number" | "code" | null; message: string };

/** Whole seconds to wait, from a server value that may be missing or junk. */
function secondsFrom(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.ceil(value) : 0;
}

/**
 * Add a phone number and prove ownership of it with a texted code.
 *
 * The state is derived from the user record (`phone_number`, `phone_verified`)
 * plus the last status the API returned — the record is the truth, and the
 * status is the fresher copy right after a send or a verify.
 *
 * There is deliberately no "run once" ref here. Every request is user-initiated
 * and guarded by a busy flag, so the React 18 StrictMode effect trap that bit
 * the email-verification page (a once-ref fighting an unmount flag, leaving the
 * component hung) has nothing to bite.
 */
export function PhoneSection() {
  const { user, refreshUser } = useAuth();
  const { success, error } = useToast();

  const [status, setStatus] = useState<PhoneVerificationStatusResponse | null>(null);
  const [phoneInput, setPhoneInput] = useState("");
  const [code, setCode] = useState("");
  const [editing, setEditing] = useState(false);
  const [sending, setSending] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [blocked, setBlocked] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [removeOpen, setRemoveOpen] = useState(false);
  // Set when a `DELETE` succeeded. The cached user record still carries the
  // number until `refreshUser` lands, and that call is allowed to fail —
  // rendering the deleted number afterwards would invite the user to verify
  // something that no longer exists.
  const [removed, setRemoved] = useState(false);

  // The number this section submitted. Kept locally because the 202 response
  // only *may* echo it back: `normalizePhoneStatus` turns a missing value into
  // null, and without this fallback a successful "Send code" would leave the
  // user staring at the entry form with no way to reach the code field.
  const [submitted, setSubmitted] = useState<string | null>(null);

  // Codes already auto-submitted. A ref, not state: it must survive re-renders
  // without causing one, and is read only from the change handler.
  const autoSubmitted = useRef<string | null>(null);

  const number = removed ? null : status?.phone_number ?? submitted ?? user?.phone_number ?? null;
  // OR rather than a plain read: the verify response is fresher than the user
  // record, and `refreshUser` swallows a failure — so the local status is what
  // makes the section flip to "verified" even when that re-read does not land.
  const verified = Boolean(user?.phone_verified || status?.phone_verified);
  const showEntry = editing || !number;

  // Countdown to the next allowed resend. The API suppresses a too-early resend
  // *silently* (it answers 202 and sends nothing, so the endpoint cannot be used
  // to probe), which means that without this the button reports success, no text
  // arrives, and the user concludes the page is broken.
  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setInterval(() => setCooldown((s) => Math.max(0, s - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [cooldown]);

  /** Adopt a status response after a code was sent (or a number was verified). */
  function applyStatus(res: PhoneVerificationStatusResponse) {
    setStatus(res);
    setRemoved(false);
    setCooldown(secondsFrom(res.resend_available_in_seconds));
    setCode("");
    // The old code is gone: a fresh one may be typed and auto-submitted again.
    autoSubmitted.current = null;
    setBlocked(false);
    setFailure(null);
    setEditing(false);
  }

  async function sendCode(e: FormEvent) {
    e.preventDefault();
    if (sending) return;

    const value = normalizePhoneInput(phoneInput);
    if (!isE164(value)) {
      // Refused before the request: the API would answer the same thing, but the
      // correction is free locally and the copy is identical.
      setFailure({ field: "number", message: INVALID_PHONE_NUMBER_MESSAGE });
      return;
    }

    setSending(true);
    setFailure(null);
    try {
      const res = await api.requestPhoneVerification({ phone_number: value });
      setSubmitted(value);
      applyStatus(res);
      success(`We sent a ${PHONE_CODE_LENGTH}-digit code to that number`);
    } catch (err) {
      setFailure({ field: "number", message: phoneErrorFor(err) });
    } finally {
      setSending(false);
    }
  }

  async function resendCode() {
    if (sending || cooldown > 0) return;
    setSending(true);
    setFailure(null);
    try {
      applyStatus(await api.resendPhoneVerification());
      success("We sent a new code");
    } catch (err) {
      // `null` field: a delivery failure is not the code input's fault, so
      // marking that field invalid would point the user at the wrong thing.
      setFailure({ field: null, message: phoneErrorFor(err) });
    } finally {
      setSending(false);
    }
  }

  async function verify(value: string) {
    if (verifying || blocked) return;
    setVerifying(true);
    setFailure(null);
    try {
      applyStatus(await api.verifyPhone({ code: value }));
      success("Phone number verified");
      // The verify response is a phone status, not a user record, so the shell's
      // copy has to be re-read. It swallows a failure, which is why `status`
      // above already flips the UI on its own.
      await refreshUser();
    } catch (err) {
      setFailure({ field: "code", message: phoneErrorFor(err) });
      // The code's attempts are spent, so nothing typed into that field can
      // succeed. Stop accepting input instead of letting the user keep going.
      if (attemptsExhausted(err)) setBlocked(true);
    } finally {
      setVerifying(false);
    }
  }

  function onCodeChange(raw: string) {
    const next = sanitizeCodeInput(raw);
    setCode(next);
    setFailure(null);
    if (!isCompleteCode(next) || blocked) return;

    // Submit once per distinct code. The attempt limit is five and a duplicate
    // request spends one of them for nothing, so the guard is keyed on the value
    // rather than on a "submitting" flag that a fast paste could outrun. A retry
    // of the *same* code after a failure stays available through the button.
    if (next === autoSubmitted.current) return;
    autoSubmitted.current = next;
    void verify(next);
  }

  function onVerifySubmit(e: FormEvent) {
    e.preventDefault();
    if (!isCompleteCode(code) || blocked) return;
    void verify(code);
  }

  function startChangingNumber() {
    setEditing(true);
    setPhoneInput(number ?? "");
    setFailure(null);
  }

  function cancelChange() {
    setEditing(false);
    setPhoneInput("");
    setFailure(null);
  }

  async function removeNumber() {
    if (removing) return;
    setRemoving(true);
    try {
      await api.removePhone();
      // Drop the local status too: it still says "verified" until the user
      // record is re-read, and that re-read may not land.
      setStatus(null);
      setRemoved(true);
      setSubmitted(null);
      setCode("");
      setPhoneInput("");
      setEditing(false);
      setBlocked(false);
      setCooldown(0);
      autoSubmitted.current = null;
      setRemoveOpen(false);
      success("Phone number removed");
      await refreshUser();
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not remove that number. Try again.");
    } finally {
      setRemoving(false);
    }
  }

  return (
    <Card className="p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold">Phone number</h2>
          <p className="mt-1 text-sm text-muted">
            Add a mobile number and confirm it with the {PHONE_CODE_LENGTH}-digit code we text you.
          </p>
        </div>
        <Phone size={20} className="shrink-0 text-muted" aria-hidden="true" />
      </div>

      <div className="mt-5">
        {verified && number ? (
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="flex flex-wrap items-center gap-2">
              <Badge color="var(--color-success)">Verified</Badge>
              <span className="font-mono text-sm text-on-background">
                {formatPhoneDisplay(number)}
              </span>
            </p>
            <Button
              type="button"
              variant="secondary"
              className="shrink-0"
              onClick={() => setRemoveOpen(true)}
            >
              Remove phone
            </Button>
          </div>
        ) : showEntry ? (
          <form onSubmit={sendCode} className="space-y-4">
            <Field
              label="Phone number"
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              placeholder="+14155552671"
              value={phoneInput}
              onChange={(e) => {
                setPhoneInput(e.target.value);
                setFailure(null);
              }}
              hint="International format, starting with + and the country code."
              aria-invalid={failure?.field === "number"}
              aria-describedby={failure ? ERROR_ID : undefined}
            />
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={sending}>
                {sending ? "Sending…" : "Send code"}
              </Button>
              {number && (
                <Button type="button" variant="ghost" onClick={cancelChange} disabled={sending}>
                  Cancel
                </Button>
              )}
            </div>
          </form>
        ) : (
          <form onSubmit={onVerifySubmit} className="space-y-4">
            <div className="rounded-lg border border-outline bg-surface-variant/60 p-3">
              <p className="text-sm text-muted">We sent a code to</p>
              <p className="font-mono text-sm text-on-background">{formatPhoneDisplay(number)}</p>
            </div>
            <Field
              label="Verification code"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={PHONE_CODE_LENGTH}
              value={code}
              onChange={(e) => onCodeChange(e.target.value)}
              hint="It arrives within a minute or two. We'll submit it for you once all six digits are in."
              disabled={blocked}
              aria-invalid={failure?.field === "code"}
              aria-describedby={failure ? ERROR_ID : undefined}
              className="font-mono tracking-[0.3em]"
            />
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={!isCompleteCode(code) || verifying || blocked}>
                {verifying ? "Verifying…" : "Verify"}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={resendCode}
                disabled={sending || cooldown > 0}
              >
                {cooldown > 0
                  ? `Resend code in ${cooldown}s`
                  : sending
                    ? "Sending…"
                    : "Resend code"}
              </Button>
              {!blocked && (
                <Button type="button" variant="ghost" onClick={startChangingNumber}>
                  Change number
                </Button>
              )}
            </div>
          </form>
        )}

        {/* One message element for both states. `role="alert"` because these
            failures arrive without a navigation, and the field that caused it is
            marked with `aria-invalid` + `aria-describedby` above. */}
        {failure && (
          <p id={ERROR_ID} role="alert" className="mt-3 text-xs text-error">
            {failure.message}
          </p>
        )}
      </div>

      <Modal
        open={removeOpen}
        onClose={() => setRemoveOpen(false)}
        title="Remove phone number?"
        maxWidth="max-w-sm"
      >
        <p className="text-sm text-on-background">
          We'll delete {formatPhoneDisplay(number)} from your account. You can add it again later,
          but you'll have to verify it again.
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setRemoveOpen(false)} disabled={removing}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={removeNumber} disabled={removing}>
            {removing ? "Removing…" : "Remove phone"}
          </Button>
        </div>
      </Modal>
    </Card>
  );
}

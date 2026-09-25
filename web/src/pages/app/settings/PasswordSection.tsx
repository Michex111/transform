import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { Lock } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Field } from "@/components/ui";
import { Modal } from "@/components/Modal";
import {
  currentPasswordFailureFor,
  validateCurrentPassword,
  validateNewPasswordPair,
  type ChangePasswordErrors,
} from "@/lib/changePassword";
import { MIN_PASSWORD_LENGTH } from "@/lib/passwordPolicy";

/** Everything the card renders from, so the post-submit states are testable. */
export interface PasswordSectionViewProps {
  /** The new password, as typed. */
  next: string;
  /** The confirmation of it, as typed. */
  confirm: string;
  /** Per-field messages from `validateNewPasswordPair`; empty until the first submit. */
  errors: ChangePasswordErrors;
  /** True while the confirmation dialog is up. */
  modalOpen: boolean;
  /** The old password, as typed in the dialog. */
  current: string;
  /** Field-level message under the dialog's input (a wrong old password). */
  currentError?: string;
  /** Form-level message inside the dialog (a failure that names no field). */
  formError?: string;
  /** True while the request is in flight. */
  busy: boolean;
  onNextChange: (value: string) => void;
  onConfirmChange: (value: string) => void;
  onCurrentChange: (value: string) => void;
  /** Submitting the resting form validates locally and opens the dialog. */
  onSubmit: (e: FormEvent) => void;
  /** Submitting the dialog runs the request. */
  onConfirmSubmit: (e: FormEvent) => void;
  onClose: () => void;
}

/**
 * The standalone "change password" card.
 *
 * Two steps, on purpose. The resting card asks only for the new password
 * twice, so the common path is two boxes and one button; the *old* password is
 * collected in a confirmation dialog, and nothing is sent until the user has
 * supplied it. That keeps the write behind an explicit confirmation without
 * showing three password boxes at once, and it means a stray Enter in a form
 * the user was still filling in cannot trigger it.
 *
 * A pure function of its props rather than reading state directly: the repo's
 * test environment is `node` (no jsdom, no testing-library), so `renderToString`
 * is the only renderer and it cannot fire a submit. Every post-submit branch
 * below could not be pinned any other way. Same split as `ResetPasswordView`.
 */
export function PasswordSectionView({
  next,
  confirm,
  errors,
  modalOpen,
  current,
  currentError,
  formError,
  busy,
  onNextChange,
  onConfirmChange,
  onCurrentChange,
  onSubmit,
  onConfirmSubmit,
  onClose,
}: PasswordSectionViewProps) {
  const currentRef = useRef<HTMLInputElement>(null);

  // Put focus back in the old-password box whenever a failure is attributed to
  // it. The input is `disabled` while the request is in flight, and disabling a
  // focused control hands focus back to `<body>` — so without this the retry
  // this message is asking for starts with a Tab to a field the user has to go
  // looking for. `busy` is a dependency as well as `currentError`, because the
  // second identical failure would otherwise not change the message and so
  // would not re-run the effect.
  useEffect(() => {
    if (currentError) currentRef.current?.focus();
  }, [currentError, busy]);

  return (
    <Card className="p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-semibold">Password</h2>
          <p className="mt-1 text-sm text-muted">
            Change the password you sign in with. You'll need your current one to confirm.
          </p>
        </div>
        <Lock size={20} className="shrink-0 text-muted" aria-hidden="true" />
      </div>

      <form onSubmit={onSubmit} className="mt-5 space-y-4">
        <Field
          id="new-password"
          label="New password"
          hint={`At least ${MIN_PASSWORD_LENGTH} characters`}
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e: ChangeEvent<HTMLInputElement>) => onNextChange(e.target.value)}
          error={errors.password}
        />
        <Field
          id="confirm-password"
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e: ChangeEvent<HTMLInputElement>) => onConfirmChange(e.target.value)}
          error={errors.confirm}
        />

        {/* The submit opens a dialog instead of writing anything, so it says so:
            a screen reader is told what is about to appear rather than being
            surprised by it. */}
        <Button type="submit" aria-haspopup="dialog" disabled={busy}>
          Update password
        </Button>
      </form>

      {/* The dialog is a SIBLING of the form above, never inside it. A nested
          `<form>` is invalid HTML and the browser resolves it unpredictably
          (the inner one is dropped or reparented), which would break
          Enter-to-submit in exactly the dialog that depends on it. */}
      <Modal
        open={modalOpen}
        onClose={onClose}
        title="Confirm your current password"
        description="Your new password is applied once you confirm the old one."
      >
        <form onSubmit={onConfirmSubmit} className="space-y-4">
          <Field
            id="current-password"
            label="Current password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e: ChangeEvent<HTMLInputElement>) => onCurrentChange(e.target.value)}
            disabled={busy}
            required
            error={currentError}
            inputRef={currentRef}
          />

          {formError && (
            // Deliberately no `role="alert"`: the toast that fires with this
            // message is the live announcement, and two alerts for one failure
            // is noise. This copy is what a keyboard user finds still attached
            // to the dialog afterwards, once the toast has gone. Same split as
            // `ProfileSection`'s save failure.
            //
            // It sits here rather than under the old-password box because the
            // failure may have nothing to do with what was typed there (a 500,
            // a timeout, a rule the *new* password broke), and pointing at the
            // wrong field is worse than saying nothing.
            <p className="text-sm text-error">{formError}</p>
          )}

          <div className="flex justify-end gap-2 pt-2">
            {/* `secondary`, not `ghost`: Escape and the backdrop also close
                this dialog, but a keyboard user who opened it by mistake needs
                a visible, focusable way out that is not the submit button. */}
            <Button type="button" variant="secondary" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? "Updating…" : "Update password"}
            </Button>
          </div>
        </form>
      </Modal>

      {/* Honest about what this does not do: there is no server-side session
          store in this deployment, so existing access tokens (30 minutes) keep
          working on other devices. Claiming a global sign-out would be a lie. */}
      <p className="mt-4 text-xs leading-relaxed text-muted">
        Other devices stay signed in until their access token expires — up to 30 minutes. If you
        think someone else has your password, change it and then sign out of those devices
        individually.
      </p>
    </Card>
  );
}

/**
 * Route component: owns the two-step state and the one request.
 *
 * No effect and no mount-time call — the request is triggered by a submit
 * inside the dialog, so a StrictMode double render has nothing to duplicate.
 */
export function PasswordSection() {
  const { success, error } = useToast();
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [current, setCurrent] = useState("");
  const [errors, setErrors] = useState<ChangePasswordErrors>({});
  const [currentError, setCurrentError] = useState<string | undefined>(undefined);
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const [modalOpen, setModalOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;

    // Local validation first: a field-level problem stops here, with no request
    // and no dialog. A toast would be the wrong surface for "the two passwords
    // don't match" — it is about one box, and it belongs under it.
    const found = validateNewPasswordPair(next, confirm);
    setErrors(found);
    if (found.password || found.confirm) return;

    // A fresh attempt starts from a clean dialog, so an error from the last try
    // is not still sitting there when the box is empty again.
    setCurrentError(undefined);
    setFormError(undefined);
    setModalOpen(true);
  }

  /**
   * Close the dialog.
   *
   * Refuses while the request is in flight (the same early return
   * `DangerZoneSection` uses for the delete dialog): a write whose result the
   * user cannot see is worse than a dialog that waits a moment. Escape and the
   * backdrop both land here, so both are disabled by it.
   *
   * The old password is cleared on every close, a cancel included, so a
   * mistyped secret does not linger in component state or in the DOM behind a
   * dialog the user reopens.
   */
  function close() {
    if (busy) return;
    setModalOpen(false);
    setCurrent("");
    setCurrentError(undefined);
    setFormError(undefined);
  }

  async function onConfirmSubmit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;

    const missing = validateCurrentPassword(current);
    if (missing) {
      setCurrentError(missing);
      return;
    }

    setCurrentError(undefined);
    setFormError(undefined);
    setBusy(true);
    try {
      await api.changePassword({ current_password: current, new_password: next });
      // Closes directly rather than through `close()`: that refuses to act
      // while busy, and this is the one path that must close while busy (it is
      // on its way to clearing `busy`).
      setModalOpen(false);
      setCurrent("");
      setNext("");
      setConfirm("");
      setErrors({});
      setCurrentError(undefined);
      setFormError(undefined);
      success("Password updated");
    } catch (err) {
      const failure = currentPasswordFailureFor(err);
      if (failure.field === "current-password") {
        // Wrong old password: stay open, put the message under the box that
        // caused it, and leave the new-password fields exactly as they are, so
        // a retry is one field rather than three. The box itself is cleared by
        // the user or by `close()`, never here — emptying the field the message
        // refers to would hide what it is about.
        setCurrentError(failure.message);
        return;
      }
      // Anything else: show it inside the dialog (which is what is on screen)
      // AND toast it. The double report is deliberate — the toast is the live
      // announcement, the inline text is what a keyboard user finds still
      // attached to the dialog afterwards, once the toast has auto-dismissed.
      // Same reasoning as `ProfileSection`'s failed save.
      setFormError(failure.message);
      error(failure.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <PasswordSectionView
      next={next}
      confirm={confirm}
      errors={errors}
      modalOpen={modalOpen}
      current={current}
      currentError={currentError}
      formError={formError}
      busy={busy}
      onNextChange={setNext}
      onConfirmChange={setConfirm}
      onCurrentChange={setCurrent}
      onSubmit={onSubmit}
      onConfirmSubmit={onConfirmSubmit}
      onClose={close}
    />
  );
}

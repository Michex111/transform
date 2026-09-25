import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { SignOut, WarningCircle } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Field } from "@/components/ui";
import { Modal } from "@/components/Modal";
import { DELETE_ACCOUNT_PHRASE } from "@/api/types";

/**
 * Irreversible actions, plus the escape hatch that should always exist beside
 * them.
 *
 * The delete flow needs the password *and* the typed word, because the only
 * other thing between a live session and a destroyed account is whoever is
 * holding the keyboard. A previous version of this card asked for confirmation
 * and then reported "not available yet" — a dead end. It is wired up for real
 * now, and the sign-out button below means deletion is never the only exit.
 */
export function DangerZoneSection() {
  const { logout } = useAuth();
  const { success, error } = useToast();
  const navigate = useNavigate();

  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);

  // Both conditions are required by the API, so the button is disabled until
  // they hold — the user cannot submit something that is guaranteed to fail.
  const ready = password.length > 0 && confirm === DELETE_ACCOUNT_PHRASE;

  function close() {
    if (busy) return;
    setOpen(false);
    setPassword("");
    setConfirm("");
  }

  async function deleteAccount() {
    if (!ready || busy) return;
    setBusy(true);
    try {
      await api.deleteAccount({ password, confirm });
      success("Your account has been deleted");
      // The session outlives the account by nothing: clear the tokens before
      // leaving, otherwise a back-navigation lands on a page that can only 401.
      logout();
      navigate("/", { replace: true });
    } catch (err) {
      // Stay open. Every common cause (a mistyped password, the wrong confirm
      // word, a dropped connection) is fixable in place, and closing the dialog
      // would throw away what the user typed.
      error(err instanceof Error ? err.message : "Could not delete your account. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="border-error/30 p-6">
      <h2 className="font-display text-lg font-semibold text-error">Danger zone</h2>
      <p className="mt-1 text-sm text-muted">
        Deleting your account removes your files, conversion history, phone number, and API keys.
        This can't be undone.
      </p>

      <div className="mt-5 flex flex-wrap gap-2">
        <Button variant="destructive" onClick={() => setOpen(true)}>
          Delete account
        </Button>
        <Button
          variant="secondary"
          onClick={() => {
            logout();
            navigate("/login");
          }}
        >
          <SignOut size={16} aria-hidden="true" />
          Sign out of this device
        </Button>
      </div>

      <Modal
        open={open}
        onClose={close}
        title="Delete your account?"
        description="This cannot be undone."
        maxWidth="max-w-md"
      >
        <div className="space-y-4">
          <p className="flex items-start gap-2 rounded-lg border border-error/40 bg-error/10 p-3 text-sm text-on-background">
            <WarningCircle
              size={18}
              weight="fill"
              className="mt-0.5 shrink-0 text-error"
              aria-hidden="true"
            />
            <span>
              Every file, conversion, and API key on this account is deleted permanently. We cannot
              restore any of it.
            </span>
          </p>

          <Field
            label="Your password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
          />
          <Field
            id="delete-confirm"
            label={`Type ${DELETE_ACCOUNT_PHRASE} to confirm`}
            autoComplete="off"
            spellCheck={false}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            disabled={busy}
            hint={`Exactly ${DELETE_ACCOUNT_PHRASE}, in capitals.`}
          />

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" onClick={close} disabled={busy}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={deleteAccount} disabled={!ready || busy}>
              {busy ? "Deleting…" : "Delete account"}
            </Button>
          </div>
        </div>
      </Modal>
    </Card>
  );
}

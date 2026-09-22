import { useEffect, useState, type FormEvent } from "react";
import { CheckCircle, Copy, Key, Trash, WarningCircle } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Field, Skeleton } from "@/components/ui";
import { Modal } from "@/components/Modal";
import type { APIKeyListItem } from "@/api/types";

export function SettingsPage() {
  const { user } = useAuth();
  const { success, error } = useToast();
  const [email, setEmail] = useState(user?.email ?? "");
  const [keys, setKeys] = useState<APIKeyListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [resending, setResending] = useState(false);
  const [newKeyName, setNewKeyName] = useState("");
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [accountDeleteOpen, setAccountDeleteOpen] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .listApiKeys()
      .then((res) => active && setKeys(res.keys))
      .catch(() => {
        /* keys may be empty */
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  function saveProfile(e: FormEvent) {
    e.preventDefault();
    if (email.trim() !== (user?.email ?? "")) {
      error("Editing your profile isn't available yet.");
    }
  }

  async function resendVerification() {
    // Deliberately uses the SAVED address, not the editable field: the resend
    // endpoint is keyed on the address, and profile editing is not wired up, so
    // resending to the field would send a link to an address the account does
    // not have.
    if (!user?.email) return;
    setResending(true);
    try {
      const result = await api.resendVerification(user.email);
      success(result.message || "Verification email sent");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not send the verification email");
    } finally {
      setResending(false);
    }
  }

  async function createKey(e: FormEvent) {
    e.preventDefault();
    try {
      const res = await api.createApiKey({ name: newKeyName || "Default key" });
      setRevealedKey(res.key);
      setNewKeyName("");
      const list = await api.listApiKeys();
      setKeys(list.keys);
      success("API key created");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not create API key");
    }
  }

  async function handleRevoke() {
    if (!revokeTarget) return;
    const id = revokeTarget;
    try {
      await api.deleteApiKey(id);
      setKeys((prev) => prev.filter((k) => k.id !== id));
      success("API key revoked");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not revoke API key");
    } finally {
      setRevokeTarget(null);
    }
  }

  async function copyKey() {
    if (!revealedKey) return;
    try {
      await navigator.clipboard.writeText(revealedKey);
      success("Key copied");
    } catch {
      /* clipboard unavailable */
    }
  }

  function deleteAccount() {
    setAccountDeleteOpen(true);
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="font-display text-2xl font-semibold">Settings</h1>

      {/* Profile */}
      <Card className="p-6">
        <h2 className="mb-4 font-display text-lg font-semibold">Profile</h2>
        <form onSubmit={saveProfile} className="space-y-4">
          <Field label="Username" value={user?.username ?? ""} disabled />
          <Field
            label="Email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />

          {/* Verification status. Reachable when enforcement is suspended (no
              email transport configured) or when the account authenticated
              with an API key — in both cases the user CAN sign in while
              unverified, so this is the only in-app way to get another link. */}
          {user?.email_verified === false ? (
            <div className="flex flex-col gap-3 rounded-lg border border-warning/40 bg-warning/10 p-3 sm:flex-row sm:items-center sm:justify-between">
              <p className="flex items-start gap-2 text-sm text-on-background">
                <WarningCircle
                  size={18}
                  weight="fill"
                  className="mt-0.5 shrink-0 text-warning"
                  aria-hidden="true"
                />
                <span>
                  Your email address is not verified yet. Open the link we emailed you to
                  activate your account.
                </span>
              </p>
              <Button
                type="button"
                variant="secondary"
                className="shrink-0"
                onClick={resendVerification}
                disabled={resending}
              >
                {resending ? "Sending…" : "Resend link"}
              </Button>
            </div>
          ) : (
            <p className="flex items-center gap-2 text-sm text-success">
              <CheckCircle size={16} weight="fill" aria-hidden="true" />
              Email verified
            </p>
          )}

          <Field label="New password" type="password" placeholder="Leave blank to keep current" />
          <Button type="submit">Save changes</Button>
        </form>
      </Card>

      {/* API keys */}
      <Card className="p-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-lg font-semibold">API keys</h2>
          <Key size={20} className="text-muted" />
        </div>

        {revealedKey && (
          <div className="mb-4 rounded-lg border border-success/40 bg-success/10 p-3">
            <p className="text-xs font-semibold text-success">Copy your key now — you won't see it again.</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="flex-1 truncate rounded border border-outline-strong bg-surface-variant px-2 py-1 font-mono text-xs text-on-background">
                {revealedKey}
              </code>
              <Button size="sm" variant="secondary" onClick={copyKey}>
                <Copy size={14} /> Copy
              </Button>
            </div>
          </div>
        )}

        <form onSubmit={createKey} className="mb-4 flex items-center gap-2">
          <input
            value={newKeyName}
            onChange={(e) => setNewKeyName(e.target.value)}
            placeholder="Key name"
            className="h-10 flex-1 rounded-lg border border-outline-strong bg-surface-variant px-3 text-sm text-on-background placeholder:text-muted focus:border-primary focus:outline-none"
          />
          <Button type="submit" size="sm">
            Create API key
          </Button>
        </form>

        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3">
                <Skeleton className="h-4 flex-1" />
                <Skeleton className="h-4 w-16" />
              </div>
            ))}
          </div>
        ) : keys.length === 0 ? (
          <p className="text-sm text-muted">No API keys yet.</p>
        ) : (
          <ul className="divide-y divide-outline">
            {keys.map((k) => (
              <li key={k.id} className="flex items-center gap-3 py-2">
                <span className="min-w-0 flex-1 truncate text-sm text-on-background">{k.name}</span>
                <span className="font-mono text-xs text-muted">{k.prefix}…</span>
                <button onClick={() => setRevokeTarget(k.id)} className="text-muted hover:text-error" aria-label={`Revoke ${k.name}`}>
                  <Trash size={16} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Danger zone */}
      <Card className="border-error/30 p-6">
        <h2 className="mb-2 font-display text-lg font-semibold text-error">Danger zone</h2>
        <p className="mb-4 text-sm text-muted">Permanently delete your account and all data.</p>
        <Button variant="destructive" onClick={deleteAccount}>
          Delete account
        </Button>
      </Card>

      {/* Revoke API key confirmation */}
      <Modal
        open={revokeTarget !== null}
        onClose={() => setRevokeTarget(null)}
        title="Revoke API key?"
        maxWidth="max-w-sm"
      >
        <p className="text-sm text-on-background">
          This key will stop working immediately and can't be restored.
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setRevokeTarget(null)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleRevoke}>
            Revoke key
          </Button>
        </div>
      </Modal>

      {/* Delete account confirmation */}
      <Modal
        open={accountDeleteOpen}
        onClose={() => setAccountDeleteOpen(false)}
        title="Delete your account?"
        description="This permanently deletes your account and all data."
        maxWidth="max-w-sm"
      >
        <p className="text-sm text-on-background">
          This removes your account, files, and history. This can't be undone.
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setAccountDeleteOpen(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              setAccountDeleteOpen(false);
              error(
                "Account deletion isn't available yet. Contact support to remove your account.",
              );
            }}
          >
            Delete account
          </Button>
        </div>
      </Modal>
    </div>
  );
}

import { useEffect, useState, type FormEvent } from "react";
import { Copy, Key, Trash } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Skeleton } from "@/components/ui";
import { Modal } from "@/components/Modal";
import type { APIKeyListItem } from "@/api/types";

/**
 * The API-keys card, unchanged in behaviour — created, revealed once, revoked
 * behind a confirmation. It moved into its own section when the settings page
 * became tabbed; nothing about the flow was re-scoped at the same time.
 */
export function ApiKeysSection() {
  const { success, error } = useToast();
  const [keys, setKeys] = useState<APIKeyListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [newKeyName, setNewKeyName] = useState("");
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
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

  return (
    <>
      <Card className="p-6">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="font-display text-lg font-semibold">API keys</h2>
          <Key size={20} className="text-muted" aria-hidden="true" />
        </div>
        <p className="mb-4 text-sm text-muted">
          Use a key to call the conversion API from your own code, without a browser session.
        </p>

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
            // The placeholder is not an accessible name, and this field had none.
            aria-label="Key name"
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
                <button
                  type="button"
                  onClick={() => setRevokeTarget(k.id)}
                  // Compact on a mouse, a full 44px target on touch. The icon
                  // alone was a 16px hit area on a phone.
                  className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-muted hover:text-error pointer-fine:h-8 pointer-fine:w-8"
                  aria-label={`Revoke ${k.name}`}
                >
                  <Trash size={16} />
                </button>
              </li>
            ))}
          </ul>
        )}
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
    </>
  );
}

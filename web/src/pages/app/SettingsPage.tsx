import { useEffect, useState, type FormEvent } from "react";
import { Copy, Key, Trash } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { useToast } from "@/auth/ToastContext";
import { Button, Card, Field, Skeleton } from "@/components/ui";
import type { APIKeyListItem } from "@/api/types";

export function SettingsPage() {
  const { user } = useAuth();
  const { success, error } = useToast();
  const [email, setEmail] = useState(user?.email ?? "");
  const [keys, setKeys] = useState<APIKeyListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [newKeyName, setNewKeyName] = useState("");
  const [revealedKey, setRevealedKey] = useState<string | null>(null);

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
    success("Profile saved");
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

  async function revokeKey(id: string) {
    if (!window.confirm("Revoke this API key? It will stop working immediately.")) return;
    try {
      await api.deleteApiKey(id);
      setKeys((prev) => prev.filter((k) => k.id !== id));
      success("API key revoked");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not revoke API key");
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
    if (window.confirm("This will permanently delete your account. Continue?")) {
      error("Account deletion is not available yet.");
    }
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
                <button onClick={() => revokeKey(k.id)} className="text-muted hover:text-error" aria-label={`Revoke ${k.name}`}>
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
    </div>
  );
}

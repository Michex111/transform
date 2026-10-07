import { useEffect, useState } from "react";
import { Plugs, PlugsConnected, Trash } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { useToast } from "@/auth/ToastContext";
import { Badge, Button, Card, Skeleton } from "@/components/ui";
import { Modal } from "@/components/Modal";
import type { ConnectedAppResponse } from "@/api/types";

/** Human wording for a scope, so the row does not print a raw identifier. */
const SCOPE_LABELS: Record<string, string> = {
  "documents.read": "Read files",
  "documents.convert": "Convert",
  "documents.write": "Save files",
  "documents.delete": "Delete files",
};

function scopeLabel(scope: string): string {
  return SCOPE_LABELS[scope] ?? scope;
}

/**
 * The connected AI applications card.
 *
 * Distinct from the API keys card beside it, and deliberately so: an API key is
 * a long-lived credential the user pastes into their own code, whereas these
 * are third-party applications the user authorized through a browser consent
 * flow. They hold scoped, revocable tokens — which is what lets a user withdraw
 * one application's access without rotating anything else.
 *
 * `client_name` comes from the application, so it is rendered as text (React's
 * default) and never as markup.
 */
export function ConnectedAppsSection() {
  const { success, error } = useToast();
  const [apps, setApps] = useState<ConnectedAppResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [revokeTarget, setRevokeTarget] = useState<ConnectedAppResponse | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    api
      .listConnectedApps()
      .then((res) => active && setApps(res.apps))
      .catch(() => {
        /* the list may simply be empty */
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  async function handleRevoke() {
    if (!revokeTarget) return;
    const id = revokeTarget.id;
    try {
      await api.revokeConnectedApp(id);
      const list = await api.listConnectedApps();
      setApps(list.apps);
      success("Access revoked");
    } catch (err) {
      error(err instanceof Error ? err.message : "Could not revoke access");
    } finally {
      setRevokeTarget(null);
    }
  }

  const active = apps.filter((a) => a.status !== "REVOKED");
  const revoked = apps.filter((a) => a.status === "REVOKED");

  return (
    <>
      <Card className="p-6">
        <div className="mb-1 flex items-center justify-between">
          <h2 className="font-display text-lg font-semibold">AI apps</h2>
          <PlugsConnected size={20} className="text-muted" aria-hidden="true" />
        </div>
        <p className="mb-4 text-sm text-muted">
          Applications you have connected so an AI assistant can read, convert and save your files.
          Revoking one takes effect immediately.
        </p>

        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 2 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3">
                <Skeleton className="h-4 flex-1" />
                <Skeleton className="h-4 w-16" />
              </div>
            ))}
          </div>
        ) : apps.length === 0 ? (
          <p className="text-sm text-muted">No applications are connected.</p>
        ) : (
          <ul className="divide-y divide-outline">
            {[...active, ...revoked].map((app) => {
              const isActive = app.status !== "REVOKED";
              return (
                <li key={app.id} className="flex items-start gap-3 py-3">
                  <Plugs
                    size={16}
                    className={`mt-1 shrink-0 ${isActive ? "text-success" : "text-muted"}`}
                    aria-hidden="true"
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-on-background">{app.client_name}</p>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {app.scopes.map((scope) => (
                        <Badge key={scope}>{scopeLabel(scope)}</Badge>
                      ))}
                      {!isActive && <Badge>Revoked</Badge>}
                    </div>
                    <p className="mt-1 text-xs text-muted">
                      {isActive
                        ? app.last_used_at
                          ? `Last used ${new Date(app.last_used_at).toLocaleDateString()}`
                          : "Not used yet"
                        : "Access revoked"}
                    </p>
                  </div>
                  {isActive && (
                    <button
                      type="button"
                      onClick={() => setRevokeTarget(app)}
                      // 44px on touch, compact on a mouse — the icon alone was
                      // a 16px hit area on a phone.
                      className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-muted hover:text-error pointer-fine:h-8 pointer-fine:w-8"
                      aria-label={`Revoke access for ${app.client_name}`}
                    >
                      <Trash size={16} />
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Modal
        open={revokeTarget !== null}
        onClose={() => setRevokeTarget(null)}
        title="Revoke access?"
        maxWidth="max-w-md"
      >
        <p className="mb-4 text-sm text-muted">
          {revokeTarget?.client_name} will lose access to your files immediately. You can connect it
          again later if you change your mind.
        </p>
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" onClick={() => setRevokeTarget(null)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleRevoke}>
            Revoke access
          </Button>
        </div>
      </Modal>
    </>
  );
}

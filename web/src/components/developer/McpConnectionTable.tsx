import { CircleNotch, PlayCircle, PauseCircle, Prohibit } from "@phosphor-icons/react";

import type { McpConnection } from "@/api/developerTypes";
import { TonePill } from "@/components/developer/TonePill";
import { Skeleton } from "@/components/ui";
import { connectionStatusMeta, formatCount } from "@/lib/developerMetrics";

/**
 * The authorized-connection list — the operational focus of the MCP page.
 *
 * Status shown here is **authorization state**, not live session connectivity: a
 * connection can be Active and idle for weeks, and claiming it is "connected"
 * because a grant exists would be a false statement about a live session. The
 * last-activity column is what actually answers "is this agent doing anything?".
 *
 * Actions are per-row and only the ones that make sense for the row's state are
 * offered, so a user is never shown a Resume for something active. Permission
 * chips expose only the scopes the server implements.
 */
export function McpConnectionTable({
  connections,
  loading,
  pendingId,
  onPause,
  onResume,
  onRevoke,
}: {
  connections: McpConnection[];
  loading: boolean;
  pendingId: string | null;
  onPause: (connection: McpConnection) => void;
  onResume: (connection: McpConnection) => void;
  onRevoke: (connection: McpConnection) => void;
}) {
  if (loading && connections.length === 0) {
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading connections">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (connections.length === 0) {
    return (
      <div className="rounded-lg border border-outline bg-surface-sunken px-6 py-10 text-center">
        <p className="text-sm font-medium text-on-background">No AI applications are connected</p>
        <p className="mx-auto mt-1 max-w-md text-xs text-muted">
          When an AI application is authorized to act on your documents through the MCP endpoint, it
          appears here with its permissions and recent activity.
        </p>
      </div>
    );
  }

  return (
    <ul className="space-y-2">
      {connections.map((connection) => {
        const status = connectionStatusMeta(connection.status);
        const pending = pendingId === connection.id;
        const granted = connection.permissions.filter((permission) => permission.granted);
        return (
          <li
            key={connection.id}
            className="rounded-lg border border-outline bg-surface p-4 transition-colors hover:border-outline-strong"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                {/* Untrusted display name from the third-party app — rendered as text. */}
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="truncate text-sm font-semibold text-on-background">
                    {connection.client_name}
                  </h3>
                  <TonePill tone={status.tone} title={status.description}>
                    {status.label}
                  </TonePill>
                </div>
                <p className="mt-1 font-mono text-[11px] text-muted" title={connection.client_id}>
                  {connection.client_id}
                </p>
              </div>

              <div className="flex shrink-0 items-center gap-2">
                {pending ? (
                  <span className="flex items-center gap-1.5 text-xs text-muted" role="status">
                    <CircleNotch size={14} className="animate-spin" aria-hidden />
                    Working…
                  </span>
                ) : null}
                {connection.status === "ACTIVE" ? (
                  <button
                    type="button"
                    onClick={() => onPause(connection)}
                    disabled={pending}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-outline-strong px-3 py-1.5 text-xs font-semibold text-on-background transition-colors hover:bg-surface-variant disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-primary"
                  >
                    <PauseCircle size={14} aria-hidden />
                    Pause
                  </button>
                ) : null}
                {connection.status === "PAUSED" ? (
                  <button
                    type="button"
                    onClick={() => onResume(connection)}
                    disabled={pending}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary transition-colors hover:bg-primary/90 disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-primary"
                  >
                    <PlayCircle size={14} aria-hidden />
                    Resume
                  </button>
                ) : null}
                {connection.status !== "REVOKED" ? (
                  <button
                    type="button"
                    onClick={() => onRevoke(connection)}
                    disabled={pending}
                    className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-error transition-colors hover:bg-error/10 disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-primary"
                  >
                    <Prohibit size={14} aria-hidden />
                    Revoke
                  </button>
                ) : null}
              </div>
            </div>

            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-4">
              <Stat label="Last activity">
                {connection.last_activity_at ? (
                  <span className="font-mono tabular-nums">
                    {new Date(connection.last_activity_at).toLocaleString(undefined, {
                      hour12: false,
                      month: "short",
                      day: "numeric",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                ) : (
                  <span className="text-muted">No calls yet</span>
                )}
              </Stat>
              <Stat label="Requests (period)">
                <span className="font-mono tabular-nums">{formatCount(connection.requests)}</span>
              </Stat>
              <Stat label="Errors">
                <span className="font-mono tabular-nums">{formatCount(connection.errors)}</span>
              </Stat>
              <Stat label="Denied">
                <span className="font-mono tabular-nums">{formatCount(connection.denied)}</span>
              </Stat>
            </dl>

            <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-outline pt-3">
              <span className="text-[11px] uppercase tracking-wide text-muted">Permissions</span>
              {granted.length === 0 ? (
                <span className="text-xs text-muted">None granted</span>
              ) : (
                granted.map((permission) => (
                  <span
                    key={permission.scope}
                    title={permission.description}
                    className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${
                      permission.destructive
                        ? "border-error/30 bg-error-container text-on-error-container"
                        : "border-outline bg-surface-variant text-muted"
                    }`}
                  >
                    {permission.scope}
                  </span>
                ))
              )}
            </div>

            {connection.status === "PAUSED" && connection.paused_at ? (
              <p className="mt-2 text-[11px] text-warning">
                Paused {new Date(connection.paused_at).toLocaleString(undefined, { hour12: false })}.
                Requests from this application are refused until you resume it.
              </p>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className="mt-0.5 text-on-background">{children}</dd>
    </div>
  );
}

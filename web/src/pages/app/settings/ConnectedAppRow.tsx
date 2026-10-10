import { PencilSimple, Plugs, Trash } from "@phosphor-icons/react";
import { Badge } from "@/components/ui";
import type { ConnectedAppResponse } from "@/api/types";
import {
  describeFolderConfinement,
  describeHistoryConfinement,
  scopeLabel,
} from "@/lib/connectedAppPermissions";

/**
 * One row of the AI-apps card: the connection's permissions and confinement,
 * plus the actions available on it.
 *
 * Presentational on purpose (no fetching, no state) so the markup — including
 * the rule that a revoked row offers no editing — can be rendered and asserted
 * with `renderToString`. `ConnectedAppsSection` owns the data and the dialogs.
 */
export function ConnectedAppRow({
  app,
  onEdit,
  onRevoke,
}: {
  app: ConnectedAppResponse;
  onEdit: (app: ConnectedAppResponse) => void;
  onRevoke: (app: ConnectedAppResponse) => void;
}) {
  const isRevoked = app.status === "REVOKED";
  const isPaused = app.status === "PAUSED";
  return (
    <li className="flex items-start gap-3 py-3">
      <Plugs
        size={16}
        className={`mt-1 shrink-0 ${
          isRevoked ? "text-muted" : isPaused ? "text-warning" : "text-success"
        }`}
        aria-hidden="true"
      />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-on-background">{app.client_name}</p>
        <div className="mt-1 flex flex-wrap gap-1">
          {app.scopes.map((scope) => (
            <Badge key={scope}>{scopeLabel(scope)}</Badge>
          ))}
          {/* Confinement is meaningless once access is revoked, so it is shown
              only while the connection could still act. */}
          {!isRevoked && <Badge>{describeFolderConfinement(app)}</Badge>}
          {!isRevoked && <Badge>{describeHistoryConfinement(app.history_scope)}</Badge>}
          {isPaused && <Badge>Paused</Badge>}
          {isRevoked && <Badge>Revoked</Badge>}
        </div>
        <p className="mt-1 text-xs text-muted">
          {isRevoked
            ? // Say what revoked means and what the user would have to do to
              // use the app again — it cannot be edited back to life.
              "Access revoked — reconnect it from the app to use it again."
            : isPaused
              ? "Paused — requests from this application are refused"
              : app.last_used_at
                ? `Last used ${new Date(app.last_used_at).toLocaleDateString()}`
                : "Not used yet"}
        </p>
      </div>
      {!isRevoked && (
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => onEdit(app)}
            // 44px on touch, compact on a mouse — the same sizing rule the
            // Revoke button uses, so the two actions line up.
            className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-muted hover:text-primary pointer-fine:h-8 pointer-fine:w-8"
            aria-label={`Edit permissions for ${app.client_name}`}
          >
            <PencilSimple size={16} />
          </button>
          <button
            type="button"
            onClick={() => onRevoke(app)}
            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-muted hover:text-error pointer-fine:h-8 pointer-fine:w-8"
            aria-label={`Revoke access for ${app.client_name}`}
          >
            <Trash size={16} />
          </button>
        </div>
      )}
    </li>
  );
}

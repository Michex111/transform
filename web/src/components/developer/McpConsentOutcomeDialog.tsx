import { CheckCircle, ShieldCheck } from "@phosphor-icons/react";
import { Button } from "@/components/ui";
import { Modal } from "@/components/Modal";
import type { ConsentOutcome } from "@/lib/mcpConsent";

/**
 * The step that hands the browser back to the application that asked to connect.
 *
 * WHY this is a confirmation the visitor completes rather than an automatic
 * redirect the moment the server answers:
 *
 * * The destination is normally an application we do not control, and for a
 *   desktop client it is a loopback port. If that listener has already stopped
 *   — the user took a while, the agent was restarted — an automatic redirect
 *   replaces a successful connection with the browser's connection-error page,
 *   which reads as "it failed" when nothing did.
 * * The connection *is* created the moment the server mints the grant. That
 *   fact is invisible on a page that instantly navigates away, so a visitor who
 *   is not returned cleanly has no way to know whether they are connected.
 *
 * So the outcome, the fact that it is already saved, and a control that returns
 * to the application are all on screen before the browser leaves our origin.
 * That is also the pattern a visitor already expects from a browser sign-in:
 * name the application, confirm, then hand back.
 */
export function McpConsentOutcomeDialog({
  outcome,
  onReturn,
  onManage,
}: {
  outcome: ConsentOutcome;
  /** Send the browser to the application's callback. */
  onReturn: () => void;
  /** Abandon the handoff and go manage connected applications instead. */
  onManage: () => void;
}) {
  const approved = outcome.kind === "approved";
  return (
    <Modal
      open
      onClose={onManage}
      title={approved ? `${outcome.clientName} is connected` : "Connection cancelled"}
      description={
        approved
          ? "Return to the application to start using it."
          : `${outcome.clientName} has no access.`
      }
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3">
          {approved ? (
            <CheckCircle size={20} className="mt-0.5 shrink-0 text-primary" aria-hidden="true" />
          ) : (
            <ShieldCheck size={20} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
          )}
          <p className="text-sm text-muted">
            {approved ? (
              <>
                <span className="font-semibold text-on-background">{outcome.clientName}</span> can
                now work with your files using the permissions you allowed. You can change or end
                this at any time in Settings → AI apps.
              </>
            ) : (
              <>Nothing on your account was shared and no connection was created.</>
            )}
          </p>
        </div>

        <div className="rounded-lg border border-outline-strong bg-surface-variant p-3">
          <p className="text-xs text-muted">
            {approved
              ? `The connection is already saved to your account, so if the button below does not open ${outcome.clientName}, switch back to it yourself — nothing is lost.`
              : `Switching back to ${outcome.clientName} tells it the request was refused.`}
          </p>
        </div>

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="ghost" onClick={onManage}>
            Manage AI apps
          </Button>
          <Button onClick={onReturn}>Return to {outcome.clientName}</Button>
        </div>
      </div>
    </Modal>
  );
}

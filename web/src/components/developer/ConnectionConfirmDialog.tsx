import { Modal } from "@/components/Modal";
import { Button } from "@/components/ui";
import type { McpConnection } from "@/api/developerTypes";

/**
 * Confirmation for a consequential connection change.
 *
 * Pause gets a dialog because the example in the product spec asks for one and
 * because it stops a working integration — the user should be told what will
 * happen, including the honest caveat that an operation already running is not
 * interrupted. Revoke gets a stronger one, since it is irreversible without a
 * fresh authorization.
 *
 * The wording is written to match what the backend actually does. A dialog that
 * promises to "cancel running operations" when the server does not would be
 * worse than no dialog.
 */
export function ConnectionConfirmDialog({
  action,
  connection,
  pending,
  onConfirm,
  onCancel,
}: {
  action: "pause" | "revoke";
  connection: McpConnection | null;
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const name = connection?.client_name ?? "this application";

  if (action === "pause") {
    return (
      <Modal
        open={connection !== null}
        onClose={onCancel}
        title={`Pause ${name}?`}
        description="This prevents the connection from making further requests to Transform until you resume it."
      >
        <p className="text-sm text-muted">
          Operations already running may finish — pausing blocks new requests rather than
          interrupting work in progress.
        </p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
          <Button onClick={onConfirm} disabled={pending}>
            {pending ? "Pausing…" : "Pause agent"}
          </Button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      open={connection !== null}
      onClose={onCancel}
      title={`Revoke ${name}?`}
      description="This permanently withdraws this application's access and invalidates its credentials."
    >
      <p className="text-sm text-muted">
        The application must be authorized again from scratch to regain access. This cannot be
        undone.
      </p>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button variant="destructive" onClick={onConfirm} disabled={pending}>
          {pending ? "Revoking…" : "Revoke access"}
        </Button>
      </div>
    </Modal>
  );
}

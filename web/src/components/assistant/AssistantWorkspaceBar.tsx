import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Cube, WarningCircle } from "@phosphor-icons/react";
import { useAuth } from "@/auth/AuthContext";
import type { Workspace } from "@/api/workspaceTypes";
import { Dropdown, type DropdownOption } from "@/components/Dropdown";

/**
 * The value that means "no workspace".
 *
 * A sentinel rather than the empty string: the options are keyed by it, so an
 * empty value would produce an unaddressable option id (`…-opt-`), and the
 * component is clearer for stating that "none" is a real choice rather than the
 * absence of one.
 */
const NO_WORKSPACE = "none";

/**
 * The workspace the assistant is working in, shown above the transcript.
 *
 * WHY this is visible at all: a model that runs code somewhere the user did not
 * choose is the single most surprising thing this feature can do. Showing the
 * active workspace — by name, with a link to it — makes the target a fact the
 * user can see and change rather than something inferred from the conversation.
 *
 * It sends the id with every turn, and the server treats it as *context*: every
 * tool re-checks that the user owns the workspace before acting. So an id typed
 * into the request cannot grant anything; it only decides where the assistant
 * tries to work.
 *
 * A workspace list that fails to load is not an error banner: the selector simply
 * falls back to "no workspace", which is exactly what the API does when it is
 * given none.
 */
export function AssistantWorkspaceBar({
  workspaceId,
  onChange,
}: {
  workspaceId: string | null;
  onChange: (workspaceId: string | null) => void;
}) {
  const { api: client } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await client.listWorkspaces(1, 50);
      setWorkspaces(response.workspaces);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [client]);

  useEffect(() => {
    void load();
  }, [load]);

  // A selection whose workspace disappeared (deleted in another tab) must not
  // keep being sent: the server would refuse every tool call, and the user would
  // see a run of failures with no explanation.
  useEffect(() => {
    if (!workspaceId) return;
    if (workspaces.length === 0) return;
    if (!workspaces.some((item) => item.workspace_id === workspaceId)) {
      onChange(null);
    }
  }, [workspaceId, workspaces, onChange]);

  const active = useMemo(
    () => workspaces.find((item) => item.workspace_id === workspaceId) ?? null,
    [workspaces, workspaceId],
  );

  // The list itself is the option list. Built here rather than inline so the
  // "no workspace" choice is always present even before the fetch resolves —
  // that is what makes the control usable (and labelled) on first paint.
  const options: DropdownOption[] = useMemo(
    () => [
      { value: NO_WORKSPACE, label: "None — ask me first" },
      ...workspaces.map((workspace) => ({
        value: workspace.workspace_id,
        label: workspace.name,
        icon: <Cube size={14} className="text-muted" aria-hidden />,
      })),
    ],
    [workspaces],
  );

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-outline px-3 py-2 text-xs">
      {/*
        The app's own dropdown, not a native `<select>`: a native control paints
        an OS menu that ignores every token below (`--color-surface`,
        `--color-primary-container`, the 8px radius), so this was the one
        surface in the product that visibly belonged to another design.

        The `label` renders inside the trigger, which is why there is no separate
        `<label>` element — the accessible name comes from `ariaLabel` instead.
      */}
      <Dropdown
        size="sm"
        label="Workspace"
        ariaLabel="Workspace"
        ariaDescribedBy={
          failed
            ? "assistant-workspace-failed"
            : !workspaceId
              ? "assistant-workspace-hint"
              : undefined
        }
        value={workspaceId ?? NO_WORKSPACE}
        onChange={(value) => onChange(value === NO_WORKSPACE ? null : value)}
        options={options}
      />

      {active && (
        <Link
          to={`/app/workspaces/${encodeURIComponent(active.workspace_id)}`}
          className="text-link hover:underline"
          aria-label={`Open ${active.name} on its workspace page`}
        >
          Open
        </Link>
      )}

      {!workspaceId && (
        <span id="assistant-workspace-hint" className="text-muted">
          The assistant will ask which workspace to use before running code.
        </span>
      )}

      {failed && (
        <span
          id="assistant-workspace-failed"
          role="status"
          className="flex items-center gap-1 text-warning"
        >
          <WarningCircle size={12} aria-hidden />
          Could not load your workspaces; the assistant will ask instead.
        </span>
      )}
    </div>
  );
}

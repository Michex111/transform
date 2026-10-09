import { FunnelSimple, MagnifyingGlass, Robot } from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";

import type { McpActivityEntry, McpConnection, ApiLogRange } from "@/api/developerTypes";
import { ConnectionConfirmDialog } from "@/components/developer/ConnectionConfirmDialog";
import { DeveloperPageHeader } from "@/components/developer/DeveloperPageHeader";
import { McpActivityDetailsDrawer } from "@/components/developer/McpActivityDetailsDrawer";
import { McpActivityTable } from "@/components/developer/McpActivityTable";
import { McpConnectionTable } from "@/components/developer/McpConnectionTable";
import { Dropdown } from "@/components/Dropdown";
import { Button, Card, Skeleton } from "@/components/ui";
import { useToast } from "@/auth/ToastContext";
import { useMcpActivity, useMcpConnections, useMcpControls, useMcpSummary } from "@/lib/developerHooks";
import { RANGE_OPTIONS, formatCount } from "@/lib/developerMetrics";

const STATUS_FILTERS = [
  { value: "all", label: "All statuses" },
  { value: "ACTIVE", label: "Active only" },
  { value: "PAUSED", label: "Paused only" },
  { value: "REVOKED", label: "Revoked only" },
];

const OUTCOME_FILTERS = [
  { value: "all", label: "All outcomes" },
  { value: "SUCCESS", label: "Successful" },
  { value: "ERROR", label: "Failed" },
  { value: "DENIED", label: "Permission denied" },
];

/**
 * Developer → MCP Activity.
 *
 * Two halves, deliberately: the **connection list** (who may act, and the
 * pause/resume/revoke controls) is the visual and operational focus, and the
 * **activity log** below answers "what have they been doing?". They read from
 * separate endpoints because they are separate questions — a paused connection
 * with a rich history must still show its history.
 *
 * Search and status filtering here are client-side over the loaded connection
 * list. That is a deliberate choice, not a gap: a user's authorized applications
 * are a handful, not a page of results, so server-side pagination would add a
 * round trip per keystroke to filter three rows. The *activity log* is paginated
 * server-side, because that one genuinely grows without bound.
 */
export function McpActivityPage() {
  const [range, setRange] = useState<ApiLogRange>("24h");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [toolFilter, setToolFilter] = useState<string>("all");
  const [outcomeFilter, setOutcomeFilter] = useState<string>("all");
  const [connectionFilter, setConnectionFilter] = useState<string>("all");
  const [selected, setSelected] = useState<McpActivityEntry | null>(null);
  const [confirm, setConfirm] = useState<{ action: "pause" | "revoke"; connection: McpConnection } | null>(
    null,
  );

  const toast = useToast();
  const { connections, loading, error, refresh, applyConnection } = useMcpConnections(range);
  const { summary, refresh: refreshSummary } = useMcpSummary(range);
  const activity = useMcpActivity({
    range,
    connectionId: connectionFilter === "all" ? undefined : connectionFilter,
    toolName: toolFilter === "all" ? undefined : toolFilter,
    outcome: outcomeFilter === "all" ? undefined : (outcomeFilter as "SUCCESS" | "ERROR" | "DENIED"),
  });
  const controls = useMcpControls();

  // Surface a failed control action once, then clear it — the hook keeps the
  // message so the page can show it, but a toast that repeats on every render
  // would be worse than the error itself.
  useEffect(() => {
    if (controls.error) toast.error(controls.error);
  }, [controls.error, toast]);

  const visibleConnections = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return connections.filter((connection) => {
      if (statusFilter !== "all" && connection.status !== statusFilter) return false;
      if (!needle) return true;
      return (
        connection.client_name.toLowerCase().includes(needle) ||
        connection.client_id.toLowerCase().includes(needle)
      );
    });
  }, [connections, search, statusFilter]);

  const toolOptions = useMemo(
    () => [
      { value: "all", label: "All tools" },
      ...(summary?.tools_used ?? []).map((tool) => ({ value: tool, label: tool })),
    ],
    [summary],
  );

  const connectionOptions = useMemo(
    () => [
      { value: "all", label: "All applications" },
      ...connections.map((connection) => ({
        value: connection.id,
        label: connection.client_name,
      })),
    ],
    [connections],
  );

  async function runAction(action: "pause" | "resume" | "revoke", connection: McpConnection) {
    const result = await controls.run(action, connection.id);
    if (!result) return; // the hook already surfaced the error
    // Update from the SERVER's answer, never from what we asked for.
    applyConnection(result.connection);
    toast.success(result.message);
    refreshSummary();
  }

  return (
    <div className="mx-auto max-w-6xl">
      <DeveloperPageHeader
        title="MCP Activity"
        description="Monitor the AI applications connected to your account and control their access to your documents."
        actions={
          <Button variant="secondary" onClick={refresh} disabled={loading}>
            Refresh
          </Button>
        }
      />

      {error ? (
        <div
          role="alert"
          className="mb-4 rounded-lg border border-error/40 bg-error-container/40 px-4 py-3 text-sm text-on-error-container"
        >
          {error}
        </div>
      ) : null}

      {/* Header counts. Three compact cards, not six — the connection list is the focus. */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SummaryCard label="Connected applications" value={summary ? formatCount(summary.connections) : "—"} loading={!summary} />
        <SummaryCard
          label="Active / paused"
          value={summary ? `${formatCount(summary.active_connections)} / ${formatCount(summary.paused_connections)}` : "—"}
          loading={!summary}
        />
        <SummaryCard label="Requests (period)" value={summary ? formatCount(summary.requests) : "—"} loading={!summary} />
        <SummaryCard
          label="Denied calls"
          value={summary ? formatCount(summary.denied) : "—"}
          loading={!summary}
          tone={summary && summary.denied > 0 ? "warning" : undefined}
          hint={summary && summary.errors > 0 ? `${formatCount(summary.errors)} failed` : undefined}
        />
      </div>

      <section aria-labelledby="mcp-connections-heading" className="mb-6">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h2
            id="mcp-connections-heading"
            className="mr-auto flex items-center gap-2 font-display text-lg font-semibold text-on-background"
          >
            <Robot size={18} className="text-primary" aria-hidden />
            Connected applications
          </h2>
          <Dropdown
            value={range}
            onChange={(value) => setRange(value as ApiLogRange)}
            options={RANGE_OPTIONS.map((option) => ({ value: option.value, label: option.label }))}
            ariaLabel="Activity period"
            label="Period"
          />
          <Dropdown
            value={statusFilter}
            onChange={setStatusFilter}
            options={STATUS_FILTERS}
            ariaLabel="Connection status"
            label="Status"
          />
          <label className="relative flex min-w-[12rem] items-center">
            <span className="sr-only">Search applications</span>
            <MagnifyingGlass size={14} aria-hidden className="pointer-events-none absolute left-3 text-muted" />
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search applications"
              className="h-9 w-full rounded-lg border border-outline bg-surface px-3 pl-8 text-sm text-on-background placeholder:text-muted focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary"
            />
          </label>
        </div>

        <McpConnectionTable
          connections={visibleConnections}
          loading={loading}
          pendingId={controls.pendingId}
          onPause={(connection) => setConfirm({ action: "pause", connection })}
          onResume={(connection) => void runAction("resume", connection)}
          onRevoke={(connection) => setConfirm({ action: "revoke", connection })}
        />
      </section>

      <section aria-labelledby="mcp-activity-heading">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h2
            id="mcp-activity-heading"
            className="mr-auto font-display text-lg font-semibold text-on-background"
          >
            Recent agent activity
          </h2>
          <Dropdown
            value={connectionFilter}
            onChange={(value) => setConnectionFilter(value)}
            options={connectionOptions}
            ariaLabel="Filter by application"
            label="Application"
          />
          <Dropdown
            value={toolFilter}
            onChange={setToolFilter}
            options={toolOptions}
            ariaLabel="Filter by tool"
            label="Tool"
          />
          <Dropdown
            value={outcomeFilter}
            onChange={setOutcomeFilter}
            options={OUTCOME_FILTERS}
            ariaLabel="Filter by outcome"
            label="Result"
          />
          {toolFilter !== "all" || outcomeFilter !== "all" || connectionFilter !== "all" ? (
            <Button
              variant="ghost"
              onClick={() => {
                setToolFilter("all");
                setOutcomeFilter("all");
                setConnectionFilter("all");
              }}
            >
              <FunnelSimple size={14} aria-hidden />
              Clear
            </Button>
          ) : null}
        </div>

        {activity.error ? (
          <div
            role="alert"
            className="mb-3 rounded-lg border border-error/40 bg-error-container/40 px-4 py-3 text-sm text-on-error-container"
          >
            {activity.error}
          </div>
        ) : null}

        <McpActivityTable entries={activity.items} loading={activity.loading} onSelect={setSelected} />

        {activity.hasMore ? (
          <div className="mt-3 flex justify-center">
            <Button variant="secondary" onClick={() => void activity.loadMore()} disabled={activity.loadingMore}>
              {activity.loadingMore ? "Loading…" : "Load more"}
            </Button>
          </div>
        ) : null}
      </section>

      <McpActivityDetailsDrawer entry={selected} onClose={() => setSelected(null)} />

      <ConnectionConfirmDialog
        action={confirm?.action ?? "pause"}
        connection={confirm?.connection ?? null}
        pending={controls.pendingId === confirm?.connection?.id}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          if (!confirm) return;
          const { action, connection } = confirm;
          setConfirm(null);
          void runAction(action, connection);
        }}
      />
    </div>
  );
}

function SummaryCard({
  label,
  value,
  loading,
  tone,
  hint,
}: {
  label: string;
  value: string;
  loading?: boolean;
  tone?: "warning" | "error";
  hint?: string;
}) {
  return (
    <Card className="p-3">
      <p className="text-xs text-muted">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-16" />
      ) : (
        <p
          className={`mt-1 font-display text-xl font-semibold tabular-nums ${
            tone === "error" ? "text-error" : tone === "warning" ? "text-warning" : "text-on-background"
          }`}
        >
          {value}
        </p>
      )}
      {hint && !loading ? <p className="mt-0.5 text-[11px] text-muted">{hint}</p> : null}
    </Card>
  );
}

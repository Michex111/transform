import { CaretRight } from "@phosphor-icons/react";

import type { McpActivityEntry } from "@/api/developerTypes";
import { TonePill } from "@/components/developer/TonePill";
import { Skeleton } from "@/components/ui";
import { formatLatency, toolOutcomeMeta } from "@/lib/developerMetrics";

/**
 * The MCP tool-call log.
 *
 * Separate from the API request table on purpose: the identity here is a
 * *connection* (a user's grant to one agent application) and the unit of work is
 * a *tool*, not a route. The outcome vocabulary also includes `DENIED`, which has
 * no HTTP status — it is the access control working, and it belongs in a list a
 * user reads when asking "what did my agent try?".
 */
export function McpActivityTable({
  entries,
  loading,
  onSelect,
}: {
  entries: McpActivityEntry[];
  loading: boolean;
  onSelect: (entry: McpActivityEntry) => void;
}) {
  if (loading && entries.length === 0) {
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading activity">
        {Array.from({ length: 5 }).map((_, index) => (
          <Skeleton key={index} className="h-11 w-full" />
        ))}
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="rounded-lg border border-outline bg-surface-sunken px-6 py-10 text-center">
        <p className="text-sm font-medium text-on-background">No agent activity in this period</p>
        <p className="mt-1 text-xs text-muted">
          Tool calls made by your connected AI applications appear here as they happen.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-outline">
      <table className="w-full min-w-[46rem] border-collapse text-sm">
        <caption className="sr-only">
          MCP tool invocations in the selected period, most recent first. Select a row for details.
        </caption>
        <thead>
          <tr className="border-b border-outline bg-surface-sunken text-left">
            <Th>Time</Th>
            <Th>Application</Th>
            <Th>Tool</Th>
            <Th>Outcome</Th>
            <Th align="right">Duration</Th>
            <Th align="right" label="Details" />
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const outcome = toolOutcomeMeta(entry.outcome);
            return (
              <tr
                key={entry.id}
                tabIndex={0}
                onClick={() => onSelect(entry)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(entry);
                  }
                }}
                className="cursor-pointer border-b border-outline/60 last:border-b-0 hover:bg-surface-variant/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
              >
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs tabular-nums text-muted">
                  {new Date(entry.timestamp).toLocaleTimeString(undefined, { hour12: false })}
                </td>
                <td className="max-w-[14rem] truncate px-3 py-2 text-xs text-on-background">
                  {entry.client_name ?? entry.client_id}
                </td>
                <td className="px-3 py-2 font-mono text-xs text-on-background">{entry.tool_name}</td>
                <td className="px-3 py-2">
                  <TonePill tone={outcome.tone} title={outcome.description}>
                    {outcome.label}
                  </TonePill>
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-xs tabular-nums text-muted">
                  {formatLatency(entry.duration_ms)}
                </td>
                <td className="px-3 py-2 text-right text-muted">
                  <CaretRight size={12} aria-hidden />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Th({
  children,
  align = "left",
  label,
}: {
  children?: React.ReactNode;
  align?: "left" | "right";
  label?: string;
}) {
  return (
    <th
      scope="col"
      aria-label={label}
      className={`px-3 py-2 text-xs font-medium uppercase tracking-wide text-muted ${
        align === "right" ? "text-right" : "text-left"
      }`}
    >
      {children}
    </th>
  );
}

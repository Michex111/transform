import { CaretRight } from "@phosphor-icons/react";

import type { ApiLogEntry } from "@/api/developerTypes";
import { TonePill } from "@/components/developer/TonePill";
import { Skeleton } from "@/components/ui";
import { formatBytes, formatLatency, logStatusMeta } from "@/lib/developerMetrics";

/**
 * The request log table.
 *
 * A real `<table>` (not a grid of divs) so a screen reader gets row/column
 * association for free, and wrapped in `overflow-x-auto` rather than
 * `overflow-hidden` — a horizontally scrolled table is usable on a phone, a
 * clipped one silently hides columns.
 *
 * Rows are buttons-in-disguise: the whole row opens the details drawer, and the
 * row is focusable with a visible focus ring. The route cell is monospace and
 * truncates with the full template in `title`, because a route template is a
 * technical string a user may need to read exactly.
 */
export function ApiLogTable({
  entries,
  loading,
  onSelect,
  emptyMessage,
}: {
  entries: ApiLogEntry[];
  loading: boolean;
  onSelect: (entry: ApiLogEntry) => void;
  emptyMessage?: string;
}) {
  if (loading && entries.length === 0) {
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading requests">
        {Array.from({ length: 6 }).map((_, index) => (
          <Skeleton key={index} className="h-11 w-full" />
        ))}
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="rounded-lg border border-outline bg-surface-sunken px-6 py-10 text-center">
        <p className="text-sm font-medium text-on-background">
          {emptyMessage ?? "No requests match these filters"}
        </p>
        <p className="mt-1 text-xs text-muted">
          Requests made with your API keys appear here as they are processed.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-outline">
      <table className="w-full min-w-[52rem] border-collapse text-sm">
        <caption className="sr-only">
          API requests in the selected period, most recent first. Select a row for details.
        </caption>
        <thead>
          <tr className="border-b border-outline bg-surface-sunken text-left">
            <Th>Time</Th>
            <Th>Method</Th>
            <Th>Endpoint</Th>
            <Th>Status</Th>
            <Th align="right">Latency</Th>
            <Th>API key</Th>
            {/* The details column's name comes from the cell's `aria-label`,
                not from an `sr-only` span: Tailwind's `sr-only` is
                `position: absolute`, and an absolutely positioned element
                inside a horizontally scrolling table escapes the scroll
                container and widens the whole document (measured: 361px of page
                overflow at a 390px viewport). */}
            <Th align="right" label="Details" />
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => {
            const status = logStatusMeta(entry.status_code);
            const responseBytes = formatBytes(entry.response_bytes);
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
                <td className="px-3 py-2 font-mono text-xs font-medium text-on-background">
                  {entry.method}
                </td>
                <td className="max-w-[20rem] px-3 py-2">
                  <span
                    title={entry.route}
                    className="block truncate font-mono text-xs text-on-background"
                  >
                    {entry.route}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <TonePill tone={status.tone} title={`HTTP ${status.label}`}>
                    {status.label}
                  </TonePill>
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-xs tabular-nums text-muted">
                  {formatLatency(entry.duration_ms)}
                </td>
                <td className="px-3 py-2 text-xs text-muted">
                  {entry.api_key_name ?? (
                    <span title="Authenticated with the dashboard session, not an API key">
                      Dashboard session
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  <span className="inline-flex items-center gap-1 text-xs text-muted">
                    {responseBytes ? <span className="tabular-nums">{responseBytes}</span> : null}
                    <CaretRight size={12} aria-hidden />
                  </span>
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

import { MagnifyingGlass, ArrowsClockwise, FunnelSimple } from "@phosphor-icons/react";

import { Dropdown } from "@/components/Dropdown";
import { Button } from "@/components/ui";
import { RANGE_OPTIONS } from "@/lib/developerMetrics";
import type { ApiLogFilters, ApiLogRange } from "@/api/developerTypes";

const METHOD_OPTIONS = [
  { value: "all", label: "All methods" },
  { value: "GET", label: "GET" },
  { value: "POST", label: "POST" },
  { value: "PATCH", label: "PATCH" },
  { value: "PUT", label: "PUT" },
  { value: "DELETE", label: "DELETE" },
];

const STATUS_OPTIONS = [
  { value: "all", label: "Any status" },
  { value: "2xx", label: "2xx success" },
  { value: "3xx", label: "3xx redirect" },
  { value: "4xx", label: "4xx client error" },
  { value: "5xx", label: "5xx server error" },
  { value: "429", label: "429 rate limited" },
];

const OUTCOME_OPTIONS = [
  { value: "all", label: "All outcomes" },
  { value: "success", label: "Successful" },
  { value: "error", label: "Errors" },
];

/**
 * The filter bar shared by the chart and the log table.
 *
 * One filter state, one request each — so narrowing the table narrows the chart
 * identically. That is the whole reason the two are driven from a single object
 * rather than each keeping its own controls: a chart that still showed all
 * traffic while the table showed one key would silently contradict itself.
 */
export function ApiLogFilterBar({
  filters,
  onChange,
  onRefresh,
  refreshing,
  apiKeyOptions,
}: {
  filters: ApiLogFilters;
  onChange: (next: ApiLogFilters) => void;
  onRefresh: () => void;
  refreshing?: boolean;
  apiKeyOptions: { value: string; label: string }[];
}) {
  const anyFilterActive =
    Boolean(filters.apiKeyId || filters.method || filters.status || filters.route || filters.requestId || filters.outcome);

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <Dropdown
        value={filters.range}
        onChange={(value) => onChange({ ...filters, range: value as ApiLogRange, cursor: undefined })}
        options={RANGE_OPTIONS.map((option) => ({ value: option.value, label: option.label }))}
        ariaLabel="Time range"
        label="Range"
      />

      <Dropdown
        value={filters.apiKeyId ?? "all"}
        onChange={(value) => onChange({ ...filters, apiKeyId: value === "all" ? undefined : value, cursor: undefined })}
        options={[{ value: "all", label: "All API keys" }, ...apiKeyOptions]}
        ariaLabel="API key"
        label="Key"
      />

      <Dropdown
        value={filters.method ?? "all"}
        onChange={(value) => onChange({ ...filters, method: value === "all" ? undefined : value, cursor: undefined })}
        options={METHOD_OPTIONS}
        ariaLabel="HTTP method"
        label="Method"
      />

      <Dropdown
        value={filters.status ?? "all"}
        onChange={(value) => onChange({ ...filters, status: value === "all" ? undefined : value, cursor: undefined })}
        options={STATUS_OPTIONS}
        ariaLabel="Status code"
        label="Status"
      />

      <Dropdown
        value={filters.outcome ?? "all"}
        onChange={(value) =>
          onChange({
            ...filters,
            outcome: value === "all" ? undefined : (value as "success" | "error"),
            cursor: undefined,
          })
        }
        options={OUTCOME_OPTIONS}
        ariaLabel="Outcome"
        label="Result"
      />

      <label className="relative flex min-w-[12rem] flex-1 items-center">
        <span className="sr-only">Filter by endpoint</span>
        <MagnifyingGlass
          size={14}
          aria-hidden
          className="pointer-events-none absolute left-3 text-muted"
        />
        <input
          type="search"
          value={filters.route ?? ""}
          onChange={(event) => onChange({ ...filters, route: event.target.value || undefined, cursor: undefined })}
          placeholder="Filter by endpoint, e.g. files"
          className="h-9 w-full rounded-lg border border-outline bg-surface px-3 pl-8 text-sm text-on-background placeholder:text-muted focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-primary"
        />
      </label>

      <Button variant="secondary" onClick={onRefresh} disabled={refreshing} aria-label="Refresh data">
        <ArrowsClockwise size={14} className={refreshing ? "animate-spin" : ""} aria-hidden />
        Refresh
      </Button>

      {anyFilterActive ? (
        <Button
          variant="ghost"
          onClick={() =>
            onChange({
              range: filters.range,
              // Deliberately keeps the range and the key: those are the two
              // selections a user makes first and most often, and clearing them
              // with "reset filters" would be surprising.
              apiKeyId: filters.apiKeyId,
            })
          }
        >
          <FunnelSimple size={14} aria-hidden />
          Clear filters
        </Button>
      ) : null}
    </div>
  );
}

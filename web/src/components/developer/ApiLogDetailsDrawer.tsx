import type { ApiLogDetailResponse } from "@/api/developerTypes";
import { Drawer } from "@/components/developer/Drawer";
import { RequestIdDisplay } from "@/components/developer/RequestIdDisplay";
import { TonePill } from "@/components/developer/TonePill";
import { Skeleton } from "@/components/ui";
import { formatBytes, formatLatency, logStatusMeta } from "@/lib/developerMetrics";

/**
 * The request details drawer.
 *
 * What it shows, and what it deliberately does not. It shows the *recorded*
 * facts: status, duration, attribution, the route template, byte counts when the
 * framework reported them, and the correlation id. It does **not** show a
 * request body, headers or a response body, because none are stored — the
 * backend records none of them and this panel must never imply otherwise.
 *
 * The "Response" section is the server's `status_meaning`, which is derived
 * from the status code. It is labelled as a status description rather than a
 * response summary so the distinction is visible, and there is no timing
 * breakdown section at all: the backend records one total duration, not spans,
 * and presenting a made-up breakdown would be fabrication.
 */
export function ApiLogDetailsDrawer({
  open,
  loading,
  detail,
  onClose,
}: {
  open: boolean;
  loading: boolean;
  detail: ApiLogDetailResponse | null;
  onClose: () => void;
}) {
  const entry = detail?.entry;
  const status = entry ? logStatusMeta(entry.status_code) : null;
  const requestBytes = formatBytes(entry?.request_bytes ?? null);
  const responseBytes = formatBytes(entry?.response_bytes ?? null);

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={
        <div>
          <p className="font-mono text-xs uppercase tracking-wider text-muted">Request details</p>
          <p className="mt-0.5 truncate font-mono text-sm text-on-background" title={entry?.route}>
            {entry ? `${entry.method} ${entry.route}` : "Loading…"}
          </p>
        </div>
      }
    >
      {loading || !entry || !status ? (
        <div className="space-y-3" aria-busy="true" aria-label="Loading request details">
          <Skeleton className="h-6 w-32" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <TonePill tone={status.tone}>{status.label}</TonePill>
            <span className="font-mono text-sm tabular-nums text-on-background">
              {formatLatency(entry.duration_ms)}
            </span>
            <TonePill tone={entry.outcome === "success" ? "success" : "error"}>{entry.outcome}</TonePill>
          </div>

          <Section title="Response">
            <p className="text-sm text-on-background">{detail.status_meaning}</p>
            <p className="mt-1 text-xs text-muted">
              Derived from the HTTP status. Response bodies are not recorded.
            </p>
          </Section>

          <Section title="Request">
            <Field label="Started">
              <span className="font-mono text-xs tabular-nums" title={entry.timestamp}>
                {new Date(entry.timestamp).toLocaleString(undefined, {
                  hour12: false,
                  dateStyle: "medium",
                  timeStyle: "medium",
                })}
              </span>
            </Field>
            <Field label="Endpoint">
              <code className="font-mono text-xs">{entry.route}</code>
            </Field>
            <Field label="Method">
              <span className="font-mono text-xs">{entry.method}</span>
            </Field>
            <Field label="Request ID">
              <RequestIdDisplay id={entry.request_id} />
            </Field>
          </Section>

          <Section title="Attribution">
            <Field label="API key">
              {entry.api_key_id ? (
                <span className="text-xs">
                  {entry.api_key_name ?? "Unnamed key"}
                  <span className="ml-1 text-muted">({entry.api_key_id})</span>
                </span>
              ) : (
                <span className="text-xs text-muted" title="Authenticated with the dashboard session">
                  Dashboard session (no API key)
                </span>
              )}
            </Field>
            {entry.environment ? (
              <Field label="Environment">
                <span className="font-mono text-xs">{entry.environment}</span>
              </Field>
            ) : null}
          </Section>

          {requestBytes || responseBytes ? (
            <Section title="Transfer">
              {requestBytes ? <Field label="Request size">{requestBytes}</Field> : null}
              {responseBytes ? <Field label="Response size">{responseBytes}</Field> : null}
            </Section>
          ) : null}

          <p className="border-t border-outline pt-3 text-[11px] leading-relaxed text-muted">
            Only metadata is recorded. This API does not store request or response bodies, headers,
            cookies, signed URLs or file contents, so none can be shown here.
          </p>
        </div>
      )}
    </Drawer>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">{title}</h3>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="shrink-0 text-xs text-muted">{label}</span>
      <span className="min-w-0 text-right text-xs text-on-background">{children}</span>
    </div>
  );
}

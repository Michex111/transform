import type { McpActivityEntry } from "@/api/developerTypes";
import { Drawer } from "@/components/developer/Drawer";
import { RequestIdDisplay } from "@/components/developer/RequestIdDisplay";
import { TonePill } from "@/components/developer/TonePill";
import { formatLatency, toolOutcomeMeta } from "@/lib/developerMetrics";

/**
 * Details for one MCP tool invocation.
 *
 * Structured audit fields only. No tool arguments, no document contents, no
 * tokens: the backend records none of them, so there is nothing to redact and
 * nothing here can be a way to read a user's files. A denied call is shown with
 * the same prominence as a failure, because a blocked attempt is exactly what a
 * user investigating "is my agent doing something it should not?" needs to see.
 */
export function McpActivityDetailsDrawer({
  entry,
  onClose,
}: {
  entry: McpActivityEntry | null;
  onClose: () => void;
}) {
  const outcome = entry ? toolOutcomeMeta(entry.outcome) : null;

  return (
    <Drawer
      open={entry !== null}
      onClose={onClose}
      title={
        <div>
          <p className="font-mono text-xs uppercase tracking-wider text-muted">Tool invocation</p>
          <p className="mt-0.5 truncate font-mono text-sm text-on-background">
            {entry?.tool_name ?? ""}
          </p>
        </div>
      }
    >
      {entry && outcome ? (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <TonePill tone={outcome.tone} title={outcome.description}>
              {outcome.label}
            </TonePill>
            {entry.duration_ms !== null ? (
              <span className="font-mono text-sm tabular-nums text-on-background">
                {formatLatency(entry.duration_ms)}
              </span>
            ) : null}
          </div>

          <p className="text-sm text-muted">{outcome.description}</p>

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
              Invocation
            </h3>
            <div className="space-y-2">
              <Field label="When">
                <span className="font-mono text-xs tabular-nums" title={entry.timestamp}>
                  {new Date(entry.timestamp).toLocaleString(undefined, {
                    hour12: false,
                    dateStyle: "medium",
                    timeStyle: "medium",
                  })}
                </span>
              </Field>
              <Field label="Tool">
                <span className="font-mono text-xs">{entry.tool_name}</span>
              </Field>
              {entry.error_category ? (
                <Field label="Failure category">
                  <span className="font-mono text-xs">{entry.error_category}</span>
                </Field>
              ) : null}
              {entry.request_id ? (
                <Field label="Correlation ID">
                  <RequestIdDisplay id={entry.request_id} label="Correlation ID" />
                </Field>
              ) : null}
            </div>
          </section>

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted">
              Authorized connection
            </h3>
            <div className="space-y-2">
              <Field label="Application">
                <span className="text-xs">{entry.client_name ?? "Unknown application"}</span>
              </Field>
              <Field label="Client ID">
                <span className="font-mono text-xs">{entry.client_id}</span>
              </Field>
              <Field label="Connection ID">
                <span className="font-mono text-xs">{entry.connection_id}</span>
              </Field>
            </div>
          </section>

          <p className="border-t border-outline pt-3 text-[11px] leading-relaxed text-muted">
            The identity above comes from the authorized connection, not from a name supplied by the
            agent. Tool arguments, prompts and document contents are never recorded.
          </p>
        </div>
      ) : null}
    </Drawer>
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

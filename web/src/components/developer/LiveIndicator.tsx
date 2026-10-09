import { CircleNotch } from "@phosphor-icons/react";

import { TonePill } from "@/components/developer/TonePill";

/**
 * The live-stream connection state.
 *
 * Announces changes through `role="status"` with `aria-live="polite"`, but the
 * *chart* deliberately does not — a screen reader should hear "connection is
 * live", not every metric tick. That split is the whole reason this is its own
 * component: the accessible announcement belongs to the state, not the data.
 *
 * The four states are distinct on purpose. "Reconnecting" is not "Disconnected"
 * (the client is retrying and the historical data on screen is still valid), and
 * neither is "Live" — telling a user their dashboard is live when the stream has
 * dropped is exactly the failure this exists to prevent.
 */

export type LiveState = "static" | "connecting" | "live" | "reconnecting" | "disconnected";

const LABELS: Record<LiveState, string> = {
  static: "Static",
  connecting: "Connecting…",
  live: "Live",
  reconnecting: "Reconnecting…",
  disconnected: "Disconnected",
};

const TONES: Record<LiveState, "success" | "warning" | "error" | "neutral" | "info"> = {
  static: "neutral",
  connecting: "info",
  live: "success",
  reconnecting: "warning",
  disconnected: "error",
};

export function LiveIndicator({ state }: { state: LiveState }) {
  const busy = state === "connecting" || state === "reconnecting";
  return (
    <span role="status" aria-live="polite" aria-atomic="true">
      <TonePill tone={TONES[state]} title={`Live metrics: ${LABELS[state]}`}>
        {busy ? <CircleNotch size={12} className="animate-spin" aria-hidden /> : null}
        <span>{LABELS[state]}</span>
      </TonePill>
    </span>
  );
}

import { describe, expect, it } from "vitest";

import {
  canSaveEnvironment,
  executionAriaLabel,
  executionOutcomeMeta,
  canStartRuntime,
  canStopRuntime,
  executionInFlight,
  executionStatusMeta,
  formatBytes,
  formatDuration,
  formatRelative,
  formatTimestamp,
  isRuntimeTransitioning,
  nextStartSummary,
  runtimeStateMeta,
} from "./workspaces";

describe("runtimeStateMeta", () => {
  it("labels every state the server can send", () => {
    expect(runtimeStateMeta("RUNNING").label).toBe("Running");
    expect(runtimeStateMeta("STARTING").tone).toBe("busy");
    expect(runtimeStateMeta("FAILED").tone).toBe("error");
  });

  it("never reports an unrecognised state as healthy", () => {
    // A state this build does not know about must not render as if all is well.
    const meta = runtimeStateMeta("TELEPORTING" as never);
    expect(meta.tone).toBe("idle");
    expect(meta.label).toBe("Unknown");
  });

  it("treats a missing runtime as stopped rather than running", () => {
    expect(runtimeStateMeta(null).label).toBe("Stopped");
    expect(runtimeStateMeta(undefined).label).toBe("Stopped");
  });
});

describe("runtime action legality", () => {
  it("offers start from stopped and failed, and not while running", () => {
    expect(canStartRuntime("STOPPED")).toBe(true);
    expect(canStartRuntime("FAILED")).toBe(true);
    expect(canStartRuntime("RUNNING")).toBe(false);
    expect(canStartRuntime("STARTING")).toBe(false);
  });

  it("offers stop only when there is something to stop", () => {
    expect(canStopRuntime("RUNNING")).toBe(true);
    expect(canStopRuntime("STARTING")).toBe(true);
    expect(canStopRuntime("STOPPED")).toBe(false);
    expect(canStopRuntime("FAILED")).toBe(false);
  });

  it("blocks a second action while a transition is in flight", () => {
    expect(isRuntimeTransitioning("STARTING")).toBe(true);
    expect(isRuntimeTransitioning("STOPPING")).toBe(true);
    expect(isRuntimeTransitioning("RUNNING")).toBe(false);
  });

  it("allows a snapshot only from a running runtime", () => {
    expect(canSaveEnvironment("RUNNING")).toBe(true);
    expect(canSaveEnvironment("STOPPED")).toBe(false);
  });
});

describe("nextStartSummary", () => {
  it("says nothing will be re-created when already running", () => {
    expect(nextStartSummary("RUNNING", true)).toContain("already running");
  });

  it("names the snapshot when one would be reused", () => {
    expect(nextStartSummary("STOPPED", true)).toContain("saved environment");
  });

  it("never claims running processes are restored", () => {
    for (const state of ["STOPPED", "FAILED", "RUNNING"] as const) {
      expect(nextStartSummary(state, true)).toContain("not restored");
    }
  });

  it("reassures rather than alarms after a failure", () => {
    expect(nextStartSummary("FAILED", false)).toContain("never touched");
  });
});

describe("execution status", () => {
  it("marks queued and running as in flight", () => {
    expect(executionInFlight("QUEUED")).toBe(true);
    expect(executionInFlight("RUNNING")).toBe(true);
  });

  it("marks terminal statuses as settled", () => {
    for (const status of ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"] as const) {
      expect(executionInFlight(status)).toBe(false);
    }
  });

  it("distinguishes a timeout from a plain failure", () => {
    expect(executionStatusMeta("TIMED_OUT").label).toBe("Timed out");
    expect(executionStatusMeta("FAILED").label).toBe("Failed");
  });
});

describe("formatting", () => {
  it("formats durations across magnitudes", () => {
    expect(formatDuration(0)).toBe("—");
    expect(formatDuration(450)).toBe("450 ms");
    expect(formatDuration(1500)).toBe("1.5 s");
    expect(formatDuration(75_000)).toBe("1m 15s");
  });

  it("formats bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(900)).toBe("900 B");
    expect(formatBytes(2048)).toBe("2.0 kB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });

  it("returns a placeholder rather than an invalid date", () => {
    expect(formatTimestamp(null)).toBe("—");
    expect(formatTimestamp("not-a-date")).toBe("—");
    expect(formatTimestamp("2026-10-13T04:00:00Z")).not.toBe("—");
  });

  it("describes recency in words", () => {
    const now = Date.parse("2026-10-13T12:00:00Z");
    expect(formatRelative(null)).toBe("never");
    expect(formatRelative("2026-10-13T11:59:30Z", now)).toBe("just now");
    expect(formatRelative("2026-10-13T11:30:00Z", now)).toBe("30 minutes ago");
    expect(formatRelative("2026-10-13T09:00:00Z", now)).toBe("3 hours ago");
    expect(formatRelative("2026-10-10T12:00:00Z", now)).toBe("3 days ago");
  });
});

describe("executionOutcomeMeta", () => {
  it("reports a clean exit as a success", () => {
    const meta = executionOutcomeMeta({ status: "SUCCEEDED", exit_code: 0 });
    expect(meta.label).toBe("Succeeded");
    expect(meta.tone).toBe("ok");
  });

  it("does not report a non-zero exit as a success", () => {
    // The defect this exists for: exit 1 rendered as a green "Finished" chip,
    // so both the list and the detail panel affirmed success for a failed run.
    const meta = executionOutcomeMeta({ status: "SUCCEEDED", exit_code: 1 });
    expect(meta.tone).toBe("error");
    expect(meta.label).toBe("Exited 1");
    expect(meta.label).not.toMatch(/finish|succeed/i);
  });

  it("names the code so a reader knows what to look up", () => {
    expect(executionOutcomeMeta({ status: "SUCCEEDED", exit_code: 137 }).label).toBe(
      "Exited 137",
    );
  });

  it("treats a missing exit code as unknown rather than successful", () => {
    const meta = executionOutcomeMeta({ status: "SUCCEEDED", exit_code: null });
    expect(meta.tone).toBe("error");
    expect(meta.label).toBe("No exit code");
  });

  it("leaves the other statuses to the plain status meta", () => {
    expect(executionOutcomeMeta({ status: "FAILED", exit_code: null }).label).toBe("Failed");
    expect(executionOutcomeMeta({ status: "TIMED_OUT", exit_code: null }).label).toBe(
      "Timed out",
    );
    expect(executionOutcomeMeta({ status: "CANCELLED", exit_code: null }).label).toBe(
      "Cancelled",
    );
  });
});

describe("executionAriaLabel", () => {
  it("includes the exit code so a screen reader can tell success from failure", () => {
    const label = executionAriaLabel(
      { status: "SUCCEEDED", exit_code: 1, mode: "SCRIPT", duration_ms: 1700 },
      "Oct 13, 07:46 AM",
    );
    expect(label).toContain("Exited 1");
    expect(label).toContain("exit code 1");
  });

  it("does not claim an exit code it was not given", () => {
    const label = executionAriaLabel(
      { status: "TIMED_OUT", exit_code: null, mode: "SCRIPT", duration_ms: 30000 },
      "noon",
    );
    expect(label).toContain("Timed out");
    expect(label).not.toContain("exit code");
  });
});

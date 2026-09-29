// Tests for the assistant header's plan-usage label, including every shape an
// older API can produce (fields absent).

import { describe, expect, it } from "vitest";
import type { AssistantStatus } from "@/api/types";
import { usageLabel, usageTitle } from "@/lib/assistantUsage";

function status(overrides: Partial<AssistantStatus> = {}): AssistantStatus {
  return { enabled: true, backend: "openai", model: "gpt-4o-mini", ...overrides };
}

describe("usageLabel", () => {
  it("shows the model tier and hourly usage when both are known", () => {
    expect(
      usageLabel(status({ model_label: "Advanced", used_this_hour: 3, requests_per_hour: 60 })),
    ).toBe("Advanced · 3/60 this hour");
  });

  it("shows only the model tier when the usage numbers are absent", () => {
    expect(usageLabel(status({ model_label: "Advanced" }))).toBe("Advanced");
    // Half a pair is not a usage figure: only the tier is shown.
    expect(usageLabel(status({ model_label: "Advanced", requests_per_hour: 60 }))).toBe("Advanced");
    expect(usageLabel(status({ model_label: "Advanced", used_this_hour: 3 }))).toBe("Advanced");
  });

  it("shows only the usage when the tier is absent", () => {
    expect(usageLabel(status({ used_this_hour: 3, requests_per_hour: 60 }))).toBe(
      "3/60 this hour",
    );
  });

  it("renders nothing when neither is known — never '0/0'", () => {
    expect(usageLabel(status())).toBeNull();
    expect(usageLabel(status({ used_this_hour: 0, requests_per_hour: 0 }))).toBeNull();
    expect(usageLabel(undefined)).toBeNull();
    expect(usageLabel(null)).toBeNull();
  });

  it("treats a blank label as absent", () => {
    expect(usageLabel(status({ model_label: "   ", used_this_hour: 1, requests_per_hour: 2 }))).toBe(
      "1/2 this hour",
    );
  });
});

describe("usageTitle", () => {
  it("expands both facts into a sentence", () => {
    expect(
      usageTitle(status({ model_label: "Advanced", used_this_hour: 3, requests_per_hour: 60 })),
    ).toBe("Advanced AI model · 3 of 60 AI requests used this hour");
  });

  it("matches usageLabel's presence, so one check covers both", () => {
    expect(usageTitle(status({ model_label: "Advanced" }))).toBe("Advanced AI model");
    expect(usageTitle(status())).toBeNull();
    expect(usageTitle(undefined)).toBeNull();
  });
});

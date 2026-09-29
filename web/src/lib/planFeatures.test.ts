// Tests for the pricing card's AI-assistant rows: the exact sentences, the
// singular/plural rule, and the "no assistant" case.

import { describe, expect, it } from "vitest";
import type { AiEntitlement } from "@/api/types";
import { aiPlanFeatures } from "@/lib/planFeatures";

function ai(overrides: Partial<AiEntitlement> = {}): AiEntitlement {
  return {
    model_level: "advanced",
    model_label: "Advanced",
    requests_per_hour: 60,
    max_attachments: 3,
    max_document_mb: 25,
    max_actions_per_turn: 8,
    ...overrides,
  };
}

describe("aiPlanFeatures", () => {
  it("renders every entitlement as a full sentence", () => {
    expect(aiPlanFeatures(ai())).toEqual([
      "Advanced AI model",
      "60 requests per hour",
      "3 attachments per message",
      "Up to 25 MB per document",
    ]);
  });

  it("uses the singular for one request, attachment and MB", () => {
    expect(
      aiPlanFeatures(ai({ requests_per_hour: 1, max_attachments: 1, max_document_mb: 1 })),
    ).toEqual([
      "Advanced AI model",
      "1 request per hour",
      "1 attachment per message",
      "Up to 1 MB per document",
    ]);
  });

  it("renders no group for a plan without an assistant", () => {
    expect(aiPlanFeatures(null)).toEqual([]);
    expect(aiPlanFeatures(undefined)).toEqual([]);
  });

  it("omits a missing/zero count rather than printing a fabricated zero", () => {
    // A plan that includes the assistant always has at least one of each, so a
    // 0 only ever means the API did not send the field.
    expect(aiPlanFeatures(ai({ requests_per_hour: 0, max_attachments: 0, max_document_mb: 0 }))).toEqual(
      ["Advanced AI model"],
    );
  });

  it("ignores a blank model label", () => {
    expect(aiPlanFeatures(ai({ model_label: "  " }))).toEqual([
      "60 requests per hour",
      "3 attachments per message",
      "Up to 25 MB per document",
    ]);
  });
});

// Tests for the assistant's deletion proposal → card mapping.
//
// The card is a decision, not a label, so the mapping has to be right in both
// directions: a live proposal must offer the buttons, and a *resolved* one must
// never offer them again (a reload must show the outcome, not a stale prompt).
// The unknown-state case is the one that matters most — a state this bundle does
// not recognise must not be rendered as a live destructive button.

import { describe, expect, it } from "vitest";
import type { AssistantArtifact } from "@/api/types";
import { deletionView, type DeletionPendingView, type DeletionResolvedView } from "@/lib/assistantDeletion";

function artifact(overrides: Partial<AssistantArtifact> = {}): AssistantArtifact {
  return {
    type: "delete",
    id: "file-1",
    name: "report.pdf",
    meta: { state: "pending", conversation_id: "c1", extension: "pdf", size_bytes: 12_345 },
    ...overrides,
  };
}

function pending(view: ReturnType<typeof deletionView>): DeletionPendingView {
  if (view.kind !== "pending") throw new Error(`expected pending, got ${view.kind}`);
  return view;
}

function resolved(view: ReturnType<typeof deletionView>): DeletionResolvedView {
  if (view.kind !== "resolved") throw new Error(`expected resolved, got ${view.kind}`);
  return view;
}

describe("deletionView", () => {
  it("maps a pending proposal to an actionable prompt", () => {
    const view = pending(deletionView(artifact()));
    expect(view.fileId).toBe("file-1");
    expect(view.conversationId).toBe("c1");
    expect(view.name).toBe("report.pdf");
    expect(view.extension).toBe("pdf");
    expect(view.sizeBytes).toBe(12_345);
    expect(view.submitting).toBe(false);
    expect(view.canSubmit).toBe(true);
    // The copy has to name the consequence, not hint at it.
    expect(view.detail).toContain("permanently deleted");
    expect(view.detail).toContain("report.pdf");
  });

  it("disables submitting while the request is in flight", () => {
    const view = pending(deletionView(artifact(), { submitting: true }));
    expect(view.submitting).toBe(true);
    expect(view.canSubmit).toBe(false);
  });

  it("cannot submit without a conversation id", () => {
    const view = pending(deletionView(artifact({ meta: { state: "pending" } })));
    expect(view.conversationId).toBeNull();
    expect(view.canSubmit).toBe(false);
  });

  it("does not offer a button once the proposal is resolved", () => {
    for (const state of ["deleted", "cancelled", "failed"]) {
      const view = resolved(deletionView(artifact({ meta: { state } })));
      expect(view.outcome).toBe(state);
      expect(view.title.length).toBeGreaterThan(0);
      expect(view.detail).toContain("report.pdf");
    }
  });

  it("names each outcome honestly", () => {
    expect(resolved(deletionView(artifact({ meta: { state: "deleted" } }))).title).toBe(
      "File deleted",
    );
    expect(resolved(deletionView(artifact({ meta: { state: "cancelled" } }))).title).toBe(
      "Deletion cancelled",
    );
    expect(resolved(deletionView(artifact({ meta: { state: "failed" } }))).title).toBe(
      "Deletion failed",
    );
  });

  it("lets the endpoint's answer override the artifact's snapshot", () => {
    // The artifact still says "pending" (it is not refetched after the click);
    // the response is the only thing that knows the decision.
    const view = resolved(deletionView(artifact(), { resolvedState: "deleted" }));
    expect(view.outcome).toBe("deleted");
  });

  it("degrades an unknown or malformed state to a non-interactive record", () => {
    for (const meta of [
      { state: "wat" },
      { state: 7 },
      { state: "  " },
      { conversation_id: "c1" },
      null,
      undefined,
    ]) {
      const view = resolved(deletionView(artifact({ meta })));
      expect(view.outcome).toBe("unknown");
      expect(view.title).toBe("Deletion status unknown");
    }
  });

  it("still renders something when the file has no name", () => {
    const blank = pending(deletionView(artifact({ name: "   " })));
    expect(blank.name).toBe("this file");
    expect(blank.detail).toContain("This file");
    const nameless = resolved(deletionView(artifact({ name: "", meta: { state: "deleted" } })));
    expect(nameless.detail).toContain("The file");
  });

  it("derives the extension from the name when the meta omits it", () => {
    const view = pending(deletionView(artifact({ meta: { state: "pending", conversation_id: "c1" } })));
    expect(view.extension).toBe("pdf");
  });

  it("ignores a nonsense size rather than rendering it", () => {
    const view = pending(
      deletionView(artifact({ meta: { state: "pending", conversation_id: "c1", size_bytes: -3 } })),
    );
    expect(view.sizeBytes).toBeNull();
  });
});

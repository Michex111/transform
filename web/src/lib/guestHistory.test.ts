// Tests for the guest history retention cap.
//
// Guest history is persisted to localStorage and used to grow without bound —
// one entry per conversion, forever — until it filled the ~5 MB quota, after
// which every write threw and was swallowed: persistence silently stopped.
// These cases pin the cap and the rule that an in-flight entry is never evicted
// (dropping one would tear down its SSE subscription).

import { describe, expect, it } from "vitest";
import type { GuestHistoryItem } from "@/api/types";
import { MAX_GUEST_HISTORY, capGuestHistory } from "@/lib/guestHistory";

function item(index: number, status = "COMPLETED"): GuestHistoryItem {
  return {
    job_id: `job-${index}`,
    guest_token: `token-${index}`,
    fileName: `file-${index}.pdf`,
    source_format: "pdf",
    target_format: "docx",
    status,
    createdAt: new Date(2026, 0, 1, 0, index).toISOString(),
  };
}

describe("capGuestHistory", () => {
  it("leaves a small history untouched", () => {
    const items = [item(3), item(2), item(1)];
    expect(capGuestHistory(items)).toEqual(items);
  });

  it("keeps the newest entries once the cap is exceeded", () => {
    // Newest first, as the hook maintains it.
    const items = Array.from({ length: 10 }, (_, i) => item(10 - i));

    const kept = capGuestHistory(items, 4);

    expect(kept.map((i) => i.job_id)).toEqual(["job-10", "job-9", "job-8", "job-7"]);
  });

  it("defaults to the exported cap", () => {
    const items = Array.from({ length: MAX_GUEST_HISTORY + 5 }, (_, i) => item(i));

    expect(capGuestHistory(items)).toHaveLength(MAX_GUEST_HISTORY);
  });

  it("never evicts an in-flight entry, however old", () => {
    const items = [
      ...Array.from({ length: 6 }, (_, i) => item(100 - i)),
      item(1, "PROCESSING"),
      item(0, "PENDING"),
    ];

    const kept = capGuestHistory(items, 2);

    expect(kept.map((i) => i.job_id)).toContain("job-1");
    expect(kept.map((i) => i.job_id)).toContain("job-0");
    expect(kept).toHaveLength(4);
  });

  it("treats AWAITING_UPLOAD as in flight, like the queue does", () => {
    const items = [item(5), item(4), item(3), item(2, "AWAITING_UPLOAD")];

    const kept = capGuestHistory(items, 1);

    expect(kept.map((i) => i.job_id)).toEqual(["job-5", "job-2"]);
  });

  it("does not mutate the input", () => {
    const items = Array.from({ length: 5 }, (_, i) => item(i));
    const before = items.length;

    capGuestHistory(items, 1);

    expect(items).toHaveLength(before);
  });
});

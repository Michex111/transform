// Tests for the copy fallback chain: prefer the async clipboard, fall back to
// execCommand when it is missing or rejects, and never throw.

import { describe, expect, it, vi } from "vitest";
import { copyText, type ClipboardEnv } from "@/lib/clipboard";

describe("copyText", () => {
  it("uses the async clipboard when it resolves", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const execCommandCopy = vi.fn().mockReturnValue(true);
    await expect(copyText("hello", { writeText, execCommandCopy })).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("hello");
    expect(execCommandCopy).not.toHaveBeenCalled();
  });

  it("falls back to execCommand when the async clipboard rejects", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    const execCommandCopy = vi.fn().mockReturnValue(true);
    await expect(copyText("hello", { writeText, execCommandCopy })).resolves.toBe(true);
    expect(execCommandCopy).toHaveBeenCalledWith("hello");
  });

  it("falls back when the async clipboard is absent (insecure origin)", async () => {
    const execCommandCopy = vi.fn().mockReturnValue(true);
    await expect(copyText("hello", { execCommandCopy })).resolves.toBe(true);
  });

  it("falls back when the async clipboard throws synchronously", async () => {
    const writeText = vi.fn(() => {
      throw new Error("nope");
    });
    const execCommandCopy = vi.fn().mockReturnValue(true);
    await expect(copyText("hello", { writeText, execCommandCopy })).resolves.toBe(true);
  });

  it("reports failure, without throwing, when every path fails", async () => {
    await expect(copyText("hello", {})).resolves.toBe(false);
    await expect(
      copyText("hello", {
        writeText: vi.fn().mockRejectedValue(new Error("x")),
        execCommandCopy: vi.fn().mockReturnValue(false),
      }),
    ).resolves.toBe(false);
    await expect(
      copyText("hello", {
        execCommandCopy: vi.fn(() => {
          throw new Error("x");
        }),
      }),
    ).resolves.toBe(false);
  });

  it("returns false for empty text without touching the environment", async () => {
    const env: ClipboardEnv = { writeText: vi.fn().mockResolvedValue(undefined) };
    await expect(copyText("", env)).resolves.toBe(false);
    expect(env.writeText).not.toHaveBeenCalled();
  });
});

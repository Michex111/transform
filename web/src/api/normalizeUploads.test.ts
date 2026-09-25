// Tests for the upload-session / storage-stats normalisers.
//
// The client declares every response shape, but nothing enforces it at runtime,
// so a `200 {}` (an older API, a proxy, a partial deploy) must degrade rather
// than crash a page. For uploads the degradation has a direction: an absent
// `upload_mode` must read as the single-PUT path the API has always supported,
// never as multipart, whose part URLs would not exist.

import { describe, expect, it } from "vitest";
import {
  normalizeStorageStats,
  normalizeUploadResponse,
  normalizeUploadSessionParts,
} from "@/api/normalize";

describe("normalizeUploadResponse", () => {
  it("defaults an absent mode to single, the path an older API supports", () => {
    const response = normalizeUploadResponse({
      upload_id: "u1",
      object_key: "k",
      upload_url: "https://store/x",
      expires_in_minutes: 60,
    });
    expect(response.upload_mode).toBe("single");
    expect(response.part_size_bytes).toBeNull();
    expect(response.part_count).toBeNull();
    expect(response.max_file_size_bytes).toBeNull();
  });

  it("treats an unrecognised mode as single too", () => {
    expect(normalizeUploadResponse({ upload_mode: "chunked" }).upload_mode).toBe("single");
    expect(normalizeUploadResponse({ upload_mode: 42 }).upload_mode).toBe("single");
  });

  it("preserves a multipart plan", () => {
    const response = normalizeUploadResponse({
      upload_id: "u1",
      object_key: "k",
      upload_url: null,
      upload_mode: "multipart",
      part_size_bytes: 104857600,
      part_count: 50,
      max_file_size_bytes: 5368709120,
    });
    expect(response.upload_mode).toBe("multipart");
    expect(response.upload_url).toBeNull();
    expect(response.part_size_bytes).toBe(104857600);
    expect(response.part_count).toBe(50);
    expect(response.max_file_size_bytes).toBe(5368709120);
  });

  it("coerces junk to safe defaults rather than propagating it", () => {
    const response = normalizeUploadResponse({
      upload_id: 5,
      upload_url: "https://store/x",
      part_size_bytes: "lots",
    });
    expect(response.upload_id).toBe("");
    expect(response.part_size_bytes).toBeNull();
  });

  it("normalises a completely empty body", () => {
    expect(normalizeUploadResponse({})).toEqual({
      upload_id: "",
      object_key: "",
      upload_url: null,
      expires_in_minutes: 0,
      upload_mode: "single",
      part_size_bytes: null,
      part_count: null,
      max_file_size_bytes: null,
    });
  });
});

describe("normalizeUploadSessionParts", () => {
  it("returns an empty list for a malformed body", () => {
    expect(normalizeUploadSessionParts({}).parts).toEqual([]);
    expect(normalizeUploadSessionParts({ parts: "nope" }).parts).toEqual([]);
  });

  it("keeps part entries and drops nothing that is well-formed", () => {
    const response = normalizeUploadSessionParts({
      parts: [{ part_number: 1, url: "https://store/p1" }],
      expires_in_minutes: 60,
    });
    expect(response.parts).toEqual([{ part_number: 1, url: "https://store/p1" }]);
    expect(response.expires_in_minutes).toBe(60);
  });
});

describe("normalizeStorageStats", () => {
  it("keeps the additive quota fields null when the API omits them", () => {
    const stats = normalizeStorageStats({ used_bytes: 1, limit_bytes: 2 });
    expect(stats.available_bytes).toBeNull();
    expect(stats.max_file_size_bytes).toBeNull();
  });

  it("preserves them when present", () => {
    const stats = normalizeStorageStats({
      used_bytes: 1,
      limit_bytes: 2,
      available_bytes: 5368709120,
      max_file_size_bytes: 5368709120,
    });
    expect(stats.available_bytes).toBe(5368709120);
    expect(stats.max_file_size_bytes).toBe(5368709120);
  });

  it("does not invent a zero for a malformed value", () => {
    const stats = normalizeStorageStats({ available_bytes: "5gb", max_file_size_bytes: null });
    expect(stats.available_bytes).toBeNull();
    expect(stats.max_file_size_bytes).toBeNull();
  });
});

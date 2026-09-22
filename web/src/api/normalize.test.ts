// Tests for the API response shape guards.
//
// These pin the exact malformed payloads that took whole pages down. Each case
// is a `200` whose body is missing a key the UI dereferences, which previously
// threw ("Cannot read properties of undefined" / "x.map is not a function")
// instead of degrading. The second half of each block asserts the normaliser is
// lossless for a well-formed payload, so this cannot quietly change good data.

import { describe, expect, it } from "vitest";
import {
  asArray,
  asBoolean,
  asNullableNumber,
  asNullableString,
  asNumber,
  asObject,
  asString,
  asStringArray,
  normalizeApiKeyCreate,
  normalizeApiKeyList,
  normalizeBatchDelete,
  normalizeCancelSubscription,
  normalizeCheckout,
  normalizeConversionHistory,
  normalizeConversionJob,
  normalizeConversionMap,
  normalizeConversionStats,
  normalizeCreditBalance,
  normalizeCreditHistory,
  normalizeCreditPricing,
  normalizeDashboard,
  normalizeFile,
  normalizeFileDownload,
  normalizeFileList,
  normalizeFolder,
  normalizeFolderContents,
  normalizeFolderList,
  normalizeGuestJob,
  normalizePortal,
  normalizePresignedUrls,
  normalizeResendVerification,
  normalizeStorageStats,
  normalizeSubscriptionPlan,
  normalizeSubscriptionPlans,
  normalizeSubscriptionStatus,
  normalizeSupportedConversions,
  normalizeTokenResponse,
  normalizeUploadResponse,
  normalizeUploadSession,
  normalizeUser,
  normalizeVerifyEmail,
} from "@/api/normalize";

describe("primitive guards", () => {
  it("asArray only accepts arrays", () => {
    expect(asArray([1, 2])).toEqual([1, 2]);
    expect(asArray(undefined)).toEqual([]);
    expect(asArray(null)).toEqual([]);
    expect(asArray({ 0: "a", length: 1 })).toEqual([]); // array-like is not an array
    expect(asArray("abc")).toEqual([]);
    expect(asArray(3)).toEqual([]);
  });

  it("asStringArray drops non-string entries", () => {
    expect(asStringArray(["a", "b"])).toEqual(["a", "b"]);
    expect(asStringArray(["a", 1, null, "b"])).toEqual(["a", "b"]);
    expect(asStringArray(undefined)).toEqual([]);
  });

  it("asNumber rejects non-finite and non-number values", () => {
    expect(asNumber(5)).toBe(5);
    expect(asNumber(0)).toBe(0);
    expect(asNumber(undefined)).toBe(0);
    expect(asNumber(undefined, 20)).toBe(20);
    expect(asNumber("7")).toBe(0); // a stringified number is not a number
    expect(asNumber(NaN)).toBe(0);
    expect(asNumber(Infinity)).toBe(0);
  });

  it("asString / asNullableString distinguish absent from empty", () => {
    expect(asString("x")).toBe("x");
    expect(asString(undefined)).toBe("");
    expect(asNullableString(undefined)).toBeNull();
    expect(asNullableString("x")).toBe("x");
  });

  it("asNullableNumber preserves null and rejects garbage", () => {
    expect(asNullableNumber(4)).toBe(4);
    expect(asNullableNumber(null)).toBeNull();
    expect(asNullableNumber(undefined)).toBeNull();
    expect(asNullableNumber("4")).toBeNull();
  });

  it("asBoolean only accepts booleans", () => {
    expect(asBoolean(true)).toBe(true);
    expect(asBoolean(undefined)).toBe(false);
    expect(asBoolean(undefined, true)).toBe(true);
    expect(asBoolean("true")).toBe(false); // the string "true" is NOT boolean
  });

  it("asObject rejects null, arrays and primitives", () => {
    expect(asObject({ a: 1 })).toEqual({ a: 1 });
    expect(asObject(null)).toEqual({});
    expect(asObject(undefined)).toEqual({});
    expect(asObject([])).toEqual({});
    expect(asObject("x")).toEqual({});
    expect(asObject(7)).toEqual({});
  });
});

describe("normalizeConversionMap", () => {
  // The reported crash: a 200 of `{}` made `conversions` undefined, and the
  // page then indexed it (`reading 'pdf'`).
  it("survives a body with no `conversions` key", () => {
    const map = normalizeConversionMap({});
    expect(map.conversions).toEqual({});
    expect(() => Object.keys(map.conversions)).not.toThrow();
    expect(map.conversions["pdf"]).toBeUndefined();
  });

  it("survives a non-object body", () => {
    expect(normalizeConversionMap(null).conversions).toEqual({});
    expect(normalizeConversionMap("nope").conversions).toEqual({});
    expect(normalizeConversionMap([]).conversions).toEqual({});
  });

  it("drops a source whose target list is not an array", () => {
    const map = normalizeConversionMap({ conversions: { pdf: "docx", png: ["jpg"] } });
    expect(map.conversions).toEqual({ pdf: [], png: ["jpg"] });
  });

  it("passes a well-formed map through unchanged", () => {
    const valid = { conversions: { pdf: ["docx", "jpg"], png: ["jpg"] } };
    expect(normalizeConversionMap(valid)).toEqual(valid);
  });
});

describe("normalizeDashboard", () => {
  // `stats?.conversion_stats.total_jobs` only guards `stats`, so a body missing
  // `conversion_stats` threw even though the call site looked defensive.
  it("guarantees the nested stats objects exist", () => {
    const d = normalizeDashboard({});
    expect(d.conversion_stats).toEqual({
      total_jobs: 0,
      successful_jobs: 0,
      failed_jobs: 0,
      total_credits_used: 0,
    });
    expect(d.storage_stats.breakdown).toEqual([]);
    expect(() => d.conversion_stats.total_jobs).not.toThrow();
  });

  it("treats a partially-populated body as zeros", () => {
    const d = normalizeDashboard({ conversion_stats: { total_jobs: 9 }, credit_balance: 12 });
    expect(d.conversion_stats.total_jobs).toBe(9);
    expect(d.conversion_stats.successful_jobs).toBe(0);
    expect(d.credit_balance).toBe(12);
    expect(d.tier).toBe("");
    expect(d.credits_reset_at).toBeNull();
  });

  it("preserves a well-formed dashboard", () => {
    const valid = {
      conversion_stats: { total_jobs: 3, successful_jobs: 2, failed_jobs: 1, total_credits_used: 8 },
      storage_stats: { used_bytes: 10, limit_bytes: 20, used_percent: 50, file_count: 1, breakdown: [] },
      credit_balance: 5,
      tier: "PRO",
      recent_jobs_count: 3,
      active_api_keys: 2,
      credits_reset_at: "2026-10-01T00:00:00Z",
    };
    expect(normalizeDashboard(valid)).toEqual(valid);
  });
});

describe("normalizeStorageStats", () => {
  it("defaults a missing breakdown to an empty array", () => {
    // The dashboard gates the breakdown UI on `breakdown.length > 0`, so `[]`
    // degrades to the plain progress bar — which is the intended fallback.
    expect(normalizeStorageStats({ used_bytes: 1 }).breakdown).toEqual([]);
    expect(normalizeStorageStats({ breakdown: "nope" }).breakdown).toEqual([]);
  });

  it("normalizes each breakdown row", () => {
    const s = normalizeStorageStats({ breakdown: [{ extension: "pdf", bytes: 5, file_count: 2 }] });
    expect(s.breakdown).toEqual([{ extension: "pdf", bytes: 5, file_count: 2 }]);
  });
});

describe("normalizeConversionHistory", () => {
  it("survives a body with no `jobs`", () => {
    const h = normalizeConversionHistory({ total: 0 });
    expect(h.jobs).toEqual([]);
    expect(() => h.jobs.map((j) => j.job_id)).not.toThrow();
  });

  it("defaults page/page_size", () => {
    const h = normalizeConversionHistory({});
    expect(h.page).toBe(1);
    expect(h.page_size).toBe(20);
  });

  it("normalizes jobs inside the list", () => {
    const h = normalizeConversionHistory({ jobs: [{ job_id: "j1", status: "COMPLETED" }], total: 1 });
    expect(h.jobs[0]).toMatchObject({ job_id: "j1", status: "COMPLETED", credits_used: 0 });
    expect(h.jobs[0].output_file).toBeNull();
  });
});

describe("normalizeGuestJob", () => {
  it("coerces a missing guest_token to an empty string", () => {
    // Prevents requests to `...?guest_token=undefined` — a clean API error
    // instead of a confusing 404/403.
    const job = normalizeGuestJob({ job_id: "g1" });
    expect(job.guest_token).toBe("");
    expect(job.job_id).toBe("g1");
  });

  it("preserves a valid guest token", () => {
    expect(normalizeGuestJob({ job_id: "g1", guest_token: "tok" }).guest_token).toBe("tok");
  });
});

describe("normalizeFileList / normalizeFolderList / normalizeFolderContents", () => {
  it("defaults the collections to empty arrays", () => {
    expect(normalizeFileList({}).files).toEqual([]);
    expect(normalizeFolderList({}).folders).toEqual([]);
    const c = normalizeFolderContents({});
    expect(c.files).toEqual([]);
    expect(c.folders).toEqual([]);
    // `folder` is always an object, so `contents.folder.name` cannot throw.
    expect(c.folder).toMatchObject({ id: "", name: "" });
  });

  it("preserves well-formed collections", () => {
    const file = {
      id: "f1",
      file_name: "a.pdf",
      file_key: "k",
      file_size_bytes: 1,
      mime_type: "application/pdf",
      folder_id: null,
      is_favorite: false,
      created_at: "2026-01-01T00:00:00Z",
      expires_at: null,
    };
    const listed = normalizeFileList({ files: [file], total: 1, page: 1, page_size: 20 });
    expect(listed.files).toEqual([file]);
  });
});

describe("normalizeApiKeyList", () => {
  // SettingsPage called `keys.length` on the result.
  it("survives a body with no `keys`", () => {
    const list = normalizeApiKeyList({});
    expect(list.keys).toEqual([]);
    expect(() => list.keys.length).not.toThrow();
  });

  it("normalizes key items", () => {
    const list = normalizeApiKeyList({ keys: [{ id: "k1", name: "ci" }] });
    expect(list.keys[0]).toMatchObject({ id: "k1", name: "ci", prefix: "", status: "" });
    expect(list.keys[0].last_used_at).toBeNull();
  });
});

describe("normalizeCreditHistory / normalizeCreditPricing / normalizeSubscriptionPlans", () => {
  it("treat a non-array body as an empty list", () => {
    // BillingPage calls `.map` on each of these.
    expect(normalizeCreditHistory({})).toEqual([]);
    expect(normalizeCreditPricing({})).toEqual([]);
    expect(normalizeSubscriptionPlans({})).toEqual([]);
  });

  it("does not throw when mapping the result", () => {
    for (const rows of [
      normalizeCreditHistory({}),
      normalizeCreditPricing({}),
      normalizeSubscriptionPlans({}),
    ]) {
      expect(() => rows.map((r) => r)).not.toThrow();
    }
  });

  it("always gives a plan a features array", () => {
    // PricingPage renders `plan.features.map(...)`.
    const p = normalizeSubscriptionPlan({ tier: "PRO", name: "Pro" });
    expect(p.features).toEqual([]);
    expect(() => p.features.map((f) => f)).not.toThrow();
  });

  it("preserves valid plan features", () => {
    const p = normalizeSubscriptionPlan({ tier: "PRO", name: "Pro", features: ["a"] });
    expect(p.features).toEqual(["a"]);
  });
});

describe("redirect + auth payloads", () => {
  it("yields blank URLs rather than navigating to the string 'undefined'", () => {
    expect(normalizeCheckout({}).checkout_url).toBe("");
    expect(normalizePortal({}).portal_url).toBe("");
    expect(normalizeCancelSubscription({}).message).toBe("");
  });

  it("preserves valid redirect URLs", () => {
    expect(normalizeCheckout({ checkout_url: "https://pay.example/x" }).checkout_url).toBe(
      "https://pay.example/x",
    );
  });

  it("never stores an undefined access token", () => {
    const t = normalizeTokenResponse({});
    expect(t.access_token).toBe("");
    expect(t.token_type).toBe("bearer");
    expect(t.refresh_token).toBeNull();
    expect(t.expires_in).toBeNull();
  });
});

describe("single-object normalizers", () => {
  it("normalizeUser gives an identity-safe default", () => {
    const u = normalizeUser({});
    expect(u.id).toBe(0);
    expect(u.username).toBe("");
    expect(u.is_active).toBe(false);
  });

  it("normalizeUser treats a missing email_verified as verified", () => {
    // The API and the SPA deploy independently, so a new bundle can talk to an
    // older API that never sends this field. Defaulting to `false` would flag
    // every account of that API as unverified, with no way to clear it.
    expect(normalizeUser({}).email_verified).toBe(true);
    expect(normalizeUser({ username: "ada" }).email_verified).toBe(true);
  });

  it("normalizeUser preserves a real unverified flag", () => {
    // The converse: an explicit `false` must survive, or the SPA would never
    // show the "resend verification" affordance it exists for.
    expect(normalizeUser({ email_verified: false }).email_verified).toBe(false);
    expect(normalizeUser({ email_verified: true }).email_verified).toBe(true);
  });

  it("normalizeUser rejects a non-boolean email_verified", () => {
    // A stringly-typed "false" from a proxy is not a boolean; fall back to the
    // safe default rather than letting a truthy string read as verified.
    expect(normalizeUser({ email_verified: "false" }).email_verified).toBe(true);
  });

  it("normalizeVerifyEmail defaults every field", () => {
    expect(normalizeVerifyEmail({})).toEqual({
      ok: false,
      already_verified: false,
      username: null,
      message: "",
    });
  });

  it("normalizeVerifyEmail keeps a real outcome", () => {
    const result = normalizeVerifyEmail({
      ok: true,
      already_verified: true,
      username: "ada",
      message: "Already verified.",
    });

    expect(result.ok).toBe(true);
    expect(result.already_verified).toBe(true);
    expect(result.username).toBe("ada");
    expect(result.message).toBe("Already verified.");
  });

  it("normalizeResendVerification keeps the message a string", () => {
    expect(normalizeResendVerification({}).message).toBe("");
    expect(normalizeResendVerification({ message: "Sent." }).message).toBe("Sent.");
  });

  it("normalizeUploadResponse keeps the upload URL a string", () => {
    const r = normalizeUploadResponse({});
    expect(r.upload_url).toBe("");
    expect(r.upload_id).toBe("");
    expect(r.expires_in_minutes).toBe(0);
  });

  it("normalizeUploadSession keeps nullable fields nullable", () => {
    const s = normalizeUploadSession({ upload_id: "u1" });
    expect(s.file_name).toBeNull();
    expect(s.folder_id).toBeNull();
  });

  it("normalizeFileDownload keeps the download URL a string", () => {
    expect(normalizeFileDownload({}).download_url).toBe("");
  });

  it("normalizeCreditBalance preserves a null allowance", () => {
    const b = normalizeCreditBalance({ balance: 3 });
    expect(b.balance).toBe(3);
    expect(b.monthly_allowance).toBeNull();
  });

  it("normalizeBatchDelete defaults the counts", () => {
    expect(normalizeBatchDelete({})).toEqual({ deleted_files: 0, deleted_folders: 0 });
  });

  it("normalizeFile / normalizeFolder default identity fields", () => {
    expect(normalizeFile({}).file_name).toBe("");
    expect(normalizeFolder({}).name).toBe("");
    expect(normalizeFolder({}).parent_id).toBeNull();
  });

  it("normalizeApiKeyCreate defaults the new key", () => {
    const k = normalizeApiKeyCreate({});
    expect(k.key).toBe("");
    expect(k.expires_at).toBeNull();
  });

  it("normalizeConversionJob erases undefined nullables", () => {
    const j = normalizeConversionJob({ job_id: "j" });
    expect(j.download_url).toBeNull();
    expect(j.object_key).toBeNull();
    expect(j.credits_used).toBe(0);
  });

  it("normalizeSubscriptionStatus defaults its fields", () => {
    const s = normalizeSubscriptionStatus({});
    expect(s.tier).toBe("");
    expect(s.current_period_end).toBeNull();
  });

  it("normalizeSupportedConversions drops a non-array body", () => {
    expect(normalizeSupportedConversions(null)).toEqual([]);
  });

  it("normalizePresignedUrls drops a non-array body", () => {
    expect(normalizePresignedUrls({})).toEqual([]);
  });

  it("normalizeConversionStats defaults every counter", () => {
    expect(normalizeConversionStats({})).toEqual({
      total_jobs: 0,
      successful_jobs: 0,
      failed_jobs: 0,
      total_credits_used: 0,
    });
  });
});

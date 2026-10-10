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
  asNullableBoolean,
  asNullableNumber,
  asNullableString,
  asNumber,
  asObject,
  asString,
  asStringArray,
  normalizeApiKeyCreate,
  normalizeApiKeyList,
  normalizeAiEntitlement,
  normalizeAssistantArtifact,
  normalizeAssistantConversation,
  normalizeAssistantConversationDetail,
  normalizeAssistantConversationList,
  normalizeAssistantDeletion,
  normalizeAssistantMessage,
  normalizeAssistantRecommend,
  normalizeAssistantStatus,
  normalizeAssistantStreamEvent,
  normalizeAssistantSummary,
  normalizeBatchDelete,
  normalizeCancelSubscription,
  normalizeChangePlan,
  normalizeCheckout,
  normalizeConversionHistory,
  normalizeConversionJob,
  normalizeConversionMap,
  normalizeConversionStats,
  normalizeCreditBalance,
  normalizeCreditHistory,
  normalizeCreditPricing,
  normalizeDashboard,
  normalizeDeleteHistoryPreview,
  normalizeDeleteHistoryRange,
  normalizeDiscountDuration,
  normalizeFile,
  normalizeFileDownload,
  normalizeFileList,
  normalizeFolder,
  normalizeFolderContents,
  normalizeFolderList,
  normalizeForgotPassword,
  normalizeGuestJob,
  normalizeJobProgressEvent,
  normalizePaymentMethodList,
  normalizePaymentMethodSession,
  normalizePhoneStatus,
  normalizePortal,
  normalizeConnectedAppList,
  normalizeMcpConsent,
  normalizeMcpFolderAccess,
  normalizePresignedUrls,
  normalizeResendVerification,
  normalizeResetPassword,
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
import { MAX_ASSISTANT_ATTACHMENTS } from "@/lib/assistantAttachments";
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
      storage_stats: {
        used_bytes: 10,
        limit_bytes: 20,
        used_percent: 50,
        file_count: 1,
        breakdown: [],
        // Additive quota fields: the normaliser is lossless for them too.
        available_bytes: 10,
        max_file_size_bytes: 5368709120,
      },
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

describe("normalizeJobProgressEvent", () => {
  it("coerces stringified numbers from a frame that crossed Redis", () => {
    // Regression: `progress: "25"` failed the client's `typeof === number`
    // guard, so the bar rendered an indeterminate sweep instead of tracking.
    const event = normalizeJobProgressEvent({
      job_id: "j1",
      status: "PROCESSING",
      progress: "25",
      message: "converting file",
      compute_duration_ms: "1200",
      credits_used: "4",
      input_size_bytes: "10",
      output_size_bytes: "5",
    });
    expect(event.progress).toBe(25);
    expect(event.compute_duration_ms).toBe(1200);
    expect(event.credits_used).toBe(4);
    expect(event.input_size_bytes).toBe(10);
    expect(event.output_size_bytes).toBe(5);
    expect(event.message).toBe("converting file");
  });

  it("omits fields the frame did not carry rather than inventing zeros", () => {
    const event = normalizeJobProgressEvent({ job_id: "j1", status: "PROCESSING" });
    expect(event.progress).toBeUndefined();
    expect(event.credits_used).toBeUndefined();
    expect(event.input_size_bytes).toBeUndefined();
  });

  it("keeps a real zero", () => {
    expect(normalizeJobProgressEvent({ progress: 0 }).progress).toBe(0);
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
    // No assistant on the payload → no AI group (not zeros, not undefined).
    expect(p.ai).toBeNull();
  });

  it("preserves valid plan features", () => {
    const p = normalizeSubscriptionPlan({ tier: "PRO", name: "Pro", features: ["a"] });
    expect(p.features).toEqual(["a"]);
  });

  it("passes a plan's AI entitlements through losslessly", () => {
    const ai = {
      model_level: "advanced",
      model_label: "Advanced",
      requests_per_hour: 60,
      max_attachments: 3,
      max_document_mb: 25,
      max_actions_per_turn: 8,
    };
    expect(normalizeSubscriptionPlan({ tier: "PRO_PLUS", ai }).ai).toEqual(ai);
  });

  it("turns an absent or unusable `ai` into null, not a half-built object", () => {
    expect(normalizeSubscriptionPlan({ tier: "FREE" }).ai).toBeNull();
    expect(normalizeSubscriptionPlan({ tier: "FREE", ai: null }).ai).toBeNull();
    expect(normalizeSubscriptionPlan({ tier: "FREE", ai: "standard" }).ai).toBeNull();
    // An object with no label cannot be described, so it is not rendered.
    expect(normalizeSubscriptionPlan({ tier: "FREE", ai: {} }).ai).toBeNull();
    expect(normalizeSubscriptionPlan({ tier: "FREE", ai: { model_label: "  " } }).ai).toBeNull();
  });
});

describe("normalizeAiEntitlement", () => {
  it("returns null for anything that is not a usable object", () => {
    expect(normalizeAiEntitlement(undefined)).toBeNull();
    expect(normalizeAiEntitlement(null)).toBeNull();
    expect(normalizeAiEntitlement([])).toBeNull();
    expect(normalizeAiEntitlement({})).toBeNull();
  });

  it("defaults missing numbers to 0 and clamps negatives", () => {
    const ai = normalizeAiEntitlement({
      model_label: "Standard",
      requests_per_hour: -5,
      max_attachments: "many",
    });
    expect(ai).toEqual({
      model_level: "",
      model_label: "Standard",
      requests_per_hour: 0,
      max_attachments: 0,
      max_document_mb: 0,
      max_actions_per_turn: 0,
    });
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

  it("carries an embedded session's client secret", () => {
    // The two shapes coexist while the API and the SPA deploy independently:
    // a hosted session answers with a URL and no secret, an embedded one the
    // other way round. Neither may be mistaken for a malformed payload.
    const embedded = normalizeCheckout({ client_secret: "cs_test_abc_secret" });
    expect(embedded.client_secret).toBe("cs_test_abc_secret");
    expect(embedded.checkout_url).toBe("");

    const hosted = normalizeCheckout({ checkout_url: "https://pay.example/x" });
    expect(hosted.client_secret).toBe("");

    // An API predating embedded checkout omits the field entirely.
    expect(normalizeCheckout({}).client_secret).toBe("");

    // A non-string secret must never reach Stripe.js as a truthy value, or the
    // page would try to mount a checkout with a secret it cannot use.
    expect(normalizeCheckout({ client_secret: 42 }).client_secret).toBe("");
    expect(normalizeCheckout({ client_secret: {} }).client_secret).toBe("");
  });
});

describe("normalizeCheckout promotion fields", () => {
  it("folds every absent discount field to null", () => {
    // The API and the SPA deploy independently, so an older API sends none of
    // these. `null` is the one "the API did not say" value that makes the order
    // summary render no discount at all — never an empty or misleading row.
    const c = normalizeCheckout({});
    expect(c.amount_total).toBeNull();
    expect(c.currency).toBeNull();
    expect(c.discount_code).toBeNull();
    expect(c.discount_percent_off).toBeNull();
    expect(c.discount_duration).toBeNull();
  });

  it("preserves a well-formed discount losslessly", () => {
    const valid = {
      checkout_url: "",
      client_secret: "cs_test_abc",
      amount_total: 0,
      currency: "usd",
      discount_code: "CAMPUS2026",
      discount_percent_off: 100,
      discount_duration: "once",
    };
    expect(normalizeCheckout(valid)).toEqual(valid);
  });

  it("treats an empty string for a string field as absent", () => {
    // `""` is not a currency and not a code; both mean the API did not send one.
    const c = normalizeCheckout({ currency: "", discount_code: "" });
    expect(c.currency).toBeNull();
    expect(c.discount_code).toBeNull();
  });

  it("rejects malformed discount primitives", () => {
    const c = normalizeCheckout({
      amount_total: "0",
      currency: 5,
      discount_code: 42,
      discount_percent_off: "100",
      discount_duration: "weekly",
    });
    expect(c.amount_total).toBeNull();
    expect(c.currency).toBeNull();
    expect(c.discount_code).toBeNull();
    expect(c.discount_percent_off).toBeNull();
    expect(c.discount_duration).toBeNull();
  });

  it("accepts exactly the three known durations", () => {
    expect(normalizeDiscountDuration("once")).toBe("once");
    expect(normalizeDiscountDuration("repeating")).toBe("repeating");
    expect(normalizeDiscountDuration("forever")).toBe("forever");
    expect(normalizeDiscountDuration("weekly")).toBeNull();
    expect(normalizeDiscountDuration(null)).toBeNull();
    expect(normalizeDiscountDuration(3)).toBeNull();
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

  it("normalizeUser degrades the profile fields of an older API", () => {
    // An API that predates the profile overhaul sends none of these. They must
    // arrive as null/"" so `lib/avatar.ts` falls back to the username-based
    // initials this app rendered before, not as `undefined` in a template.
    const u = normalizeUser({ username: "ada" });
    expect(u.first_name).toBeNull();
    expect(u.last_name).toBeNull();
    expect(u.display_name).toBe("");
    expect(u.initials).toBe("");
    expect(u.avatar_url).toBeNull();
    expect(u.phone_number).toBeNull();
  });

  it("normalizeUser treats a missing phone_verified as NOT verified", () => {
    // The opposite default to `email_verified`, deliberately: an unverified
    // phone is the normal state, whereas claiming a security control passed
    // when the API never said so would be a lie. The section just reads
    // "not verified".
    expect(normalizeUser({}).phone_verified).toBe(false);
    expect(normalizeUser({ phone_verified: "true" }).phone_verified).toBe(false);
    expect(normalizeUser({ phone_verified: true }).phone_verified).toBe(true);
  });

  it("normalizeUser folds every unset default save folder into null", () => {
    // Optional on the wire for the independent-deploy reason. An absent key, an
    // explicit null, the API's cleared value ("") and a malformed non-string
    // must all be the ONE "no preference" value, which the drive UI reads as
    // "save to the root" — never as a save against an empty folder id.
    expect(normalizeUser({}).default_save_folder_id).toBeNull();
    expect(normalizeUser({ default_save_folder_id: null }).default_save_folder_id).toBeNull();
    expect(normalizeUser({ default_save_folder_id: "" }).default_save_folder_id).toBeNull();
    expect(normalizeUser({ default_save_folder_id: 7 }).default_save_folder_id).toBeNull();
  });

  it("normalizeUser keeps a real default save folder", () => {
    expect(normalizeUser({ default_save_folder_id: "f-123" }).default_save_folder_id).toBe("f-123");
  });

  it("normalizeUser preserves a real profile", () => {
    const u = normalizeUser({
      id: 7,
      username: "ada",
      first_name: "Ada",
      last_name: "Lovelace",
      display_name: "Ada Lovelace",
      initials: "AL",
      avatar_url: "data:image/webp;base64,AAAA",
      phone_number: "+14155552671",
      phone_verified: true,
    });
    expect(u.first_name).toBe("Ada");
    expect(u.last_name).toBe("Lovelace");
    expect(u.display_name).toBe("Ada Lovelace");
    expect(u.initials).toBe("AL");
    expect(u.avatar_url).toBe("data:image/webp;base64,AAAA");
    expect(u.phone_number).toBe("+14155552671");
    expect(u.phone_verified).toBe(true);
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

  it("normalizeUploadResponse keeps the upload URL nullable and plans single by default", () => {
    // A multipart session legitimately carries no whole-object URL, so an
    // absent one must read as `null` rather than an empty string that a
    // single-PUT caller would try to fetch.
    const r = normalizeUploadResponse({});
    expect(r.upload_url).toBeNull();
    expect(r.upload_id).toBe("");
    expect(r.expires_in_minutes).toBe(0);
    expect(r.upload_mode).toBe("single");
    expect(r.part_size_bytes).toBeNull();
    expect(r.part_count).toBeNull();
    expect(r.max_file_size_bytes).toBeNull();
  });

  it("normalizeUploadSession keeps nullable fields nullable", () => {
    const s = normalizeUploadSession({ upload_id: "u1" });
    expect(s.file_name).toBeNull();
    expect(s.folder_id).toBeNull();
  });

  it("normalizeUploadSession passes through the created file_id", () => {
    // `verify` now returns the library file id so an uploaded attachment can be
    // sent as `file_ids`. It is additive: absent on an older API.
    expect(normalizeUploadSession({ upload_id: "u1", file_id: "f9" }).file_id).toBe("f9");
    // Absent (older API) must stay absent, not become `""`.
    expect("file_id" in normalizeUploadSession({ upload_id: "u1" })).toBe(false);
    // An empty string is the API's "no file" value, not a usable id.
    expect(normalizeUploadSession({ upload_id: "u1", file_id: "" }).file_id).toBeUndefined();
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

  it("normalizeConversionJob reads the persisted progress percentage", () => {
    // The persisted value is what lets a reloaded chat draw a real bar
    // immediately, instead of an indeterminate sweep until SSE replays.
    expect(normalizeConversionJob({ job_id: "j", progress: 50 }).progress).toBe(50);
    // A numeric string (a legacy cache, or an older API) still reads.
    expect(normalizeConversionJob({ job_id: "j", progress: "75" }).progress).toBe(75);
  });

  it("normalizeConversionJob treats progress 0 as not reported", () => {
    // 0 means "the worker has not started"; leaving it undefined keeps the bar
    // indeterminate (moving) rather than a frozen empty 0% bar.
    expect(normalizeConversionJob({ job_id: "j", progress: 0 }).progress).toBeUndefined();
    expect(normalizeConversionJob({ job_id: "j" }).progress).toBeUndefined();
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

describe("phone verification + history-range normalizers", () => {
  it("normalizePhoneStatus defaults an empty body", () => {
    // A 202 whose body is `{}` must still describe a usable state, and must not
    // report a verified number the API never claimed.
    expect(normalizePhoneStatus({})).toEqual({
      phone_number: null,
      phone_verified: false,
      expires_in_seconds: null,
      resend_available_in_seconds: null,
    });
  });

  it("normalizePhoneStatus preserves a real status", () => {
    expect(
      normalizePhoneStatus({
        phone_number: "+14155552671",
        phone_verified: true,
        expires_in_seconds: 600,
        resend_available_in_seconds: 60,
      }),
    ).toEqual({
      phone_number: "+14155552671",
      phone_verified: true,
      expires_in_seconds: 600,
      resend_available_in_seconds: 60,
    });
  });

  it("normalizePhoneStatus drops a non-numeric countdown", () => {
    // A countdown drives a timer, so a string would produce `NaN` seconds and a
    // button that never re-enables.
    expect(normalizePhoneStatus({ expires_in_seconds: "600" }).expires_in_seconds).toBeNull();
    expect(normalizePhoneStatus({ resend_available_in_seconds: null }).resend_available_in_seconds).toBeNull();
  });

  it("normalizeDeleteHistoryPreview defaults an empty body", () => {
    expect(normalizeDeleteHistoryPreview({})).toEqual({
      range: "24h",
      since: null,
      count: 0,
      active_count: 0,
    });
  });

  it("normalizeDeleteHistoryPreview preserves a real preview", () => {
    expect(
      normalizeDeleteHistoryPreview({
        range: "7d",
        since: "2026-09-15T00:00:00Z",
        count: 12,
        active_count: 2,
      }),
    ).toEqual({ range: "7d", since: "2026-09-15T00:00:00Z", count: 12, active_count: 2 });
  });

  it("normalizeDeleteHistoryRange coerces an unknown window instead of echoing it", () => {
    // The range is echoed into user-facing copy, so an unrecognised value must
    // not reach the screen. The count — the part the user acts on — still does.
    expect(normalizeDeleteHistoryRange({ range: "999y", deleted_count: 3 })).toEqual({
      range: "24h",
      deleted_count: 3,
      skipped_active: 0,
    });
    expect(normalizeDeleteHistoryRange({ range: "all", deleted_count: 9 }).range).toBe("all");
  });

  it("normalizeDeleteHistoryRange defaults an empty body", () => {
    expect(normalizeDeleteHistoryRange({})).toEqual({
      range: "24h",
      deleted_count: 0,
      skipped_active: 0,
    });
  });
});

describe("password reset normalizers", () => {
  it("normalizeForgotPassword keeps the confirmation a string", () => {
    // A 202 with an empty body is technically possible and must render the
    // page's own conditional copy rather than the word "undefined".
    expect(normalizeForgotPassword({}).message).toBe("");
    expect(normalizeForgotPassword(null).message).toBe("");
    expect(normalizeForgotPassword("nope").message).toBe("");
  });

  it("normalizeForgotPassword passes a well-formed body through unchanged", () => {
    const valid = {
      message:
        "If an account with that email address exists, we've sent instructions for resetting your password.",
    };
    expect(normalizeForgotPassword(valid)).toEqual(valid);
  });

  it("normalizeResetPassword defaults a malformed 200", () => {
    // The shape boundary: `200 {}` must not crash the success panel, and must
    // not claim an outcome the API never reported.
    expect(normalizeResetPassword({})).toEqual({ ok: false, username: null, message: "" });
    expect(normalizeResetPassword(null)).toEqual({ ok: false, username: null, message: "" });
    expect(normalizeResetPassword([])).toEqual({ ok: false, username: null, message: "" });
    expect(normalizeResetPassword({ ok: "true", username: 7, message: null })).toEqual({
      ok: false,
      username: null,
      message: "",
    });
  });

  it("normalizeResetPassword keeps an absent username null, never an empty string", () => {
    // The sign-in form pre-fills from this value; `""` would submit as a
    // pre-filled blank rather than leaving the field untouched.
    expect(normalizeResetPassword({ ok: true, message: "Done." }).username).toBeNull();
    expect(normalizeResetPassword({ ok: true, username: null }).username).toBeNull();
    expect(normalizeResetPassword({ username: "ada" }).username).toBe("ada");
  });

  it("normalizeResetPassword passes a well-formed body through unchanged", () => {
    const valid = {
      ok: true,
      username: "ada",
      message: "Your password has been updated. Sign in with your new password.",
    };
    expect(normalizeResetPassword(valid)).toEqual(valid);
  });
});

/* ------------------------------------------------------------------ *
 * Transform AI
 * ------------------------------------------------------------------ */

describe("normalizeAssistantStatus", () => {
  it("degrades a missing body to disabled with an unknown backend", () => {
    // Never guess a vendor name: only an exact `"echo"` may light the demo badge.
    expect(normalizeAssistantStatus(undefined)).toEqual({
      enabled: false,
      backend: "unknown",
      model: "",
      // The client's own cap, NOT 0: an older API omits the field and a 0 would
      // make the composer refuse every attachment on that API.
      max_attachments: MAX_ASSISTANT_ATTACHMENTS,
    });
    expect(normalizeAssistantStatus({ backend: "something-new" }).backend).toBe("unknown");
  });

  it("passes a well-formed body through, filling only the attachment cap", () => {
    expect(
      normalizeAssistantStatus({ enabled: true, backend: "openai", model: "gpt-4o-mini" }),
    ).toEqual({
      enabled: true,
      backend: "openai",
      model: "gpt-4o-mini",
      max_attachments: MAX_ASSISTANT_ATTACHMENTS,
    });
  });

  it("accepts the gemini backend as a real (non-demo) provider", () => {
    const status = normalizeAssistantStatus({
      enabled: true,
      backend: "gemini",
      model: "gemini-2.5-flash-lite",
    });
    expect(status.backend).toBe("gemini");
    expect(status.model).toBe("gemini-2.5-flash-lite");
  });

  it("keeps every absent entitlement absent, except max_attachments", () => {
    const status = normalizeAssistantStatus({ enabled: true, backend: "openai" });
    expect(status.tier).toBeUndefined();
    expect(status.model_label).toBeUndefined();
    expect(status.requests_per_hour).toBeUndefined();
    expect(status.used_this_hour).toBeUndefined();
    expect(status.remaining_this_hour).toBeUndefined();
    expect(status.max_actions_per_turn).toBeUndefined();
    expect(status.max_document_bytes).toBeUndefined();
    expect(status.max_attachments).toBe(MAX_ASSISTANT_ATTACHMENTS);
  });

  it("parses a full entitlement payload and clamps negatives", () => {
    const status = normalizeAssistantStatus({
      enabled: true,
      backend: "openai",
      model: "gpt-4o-mini",
      tier: "PRO_PLUS",
      model_level: "advanced",
      model_label: "Advanced",
      requests_per_hour: 60,
      used_this_hour: 3,
      remaining_this_hour: 57,
      max_attachments: 3,
      max_actions_per_turn: 8,
      max_document_bytes: 26214400,
    });
    expect(status).toMatchObject({
      tier: "PRO_PLUS",
      model_level: "advanced",
      model_label: "Advanced",
      requests_per_hour: 60,
      used_this_hour: 3,
      remaining_this_hour: 57,
      max_attachments: 3,
      max_actions_per_turn: 8,
      max_document_bytes: 26214400,
    });
    const clamped = normalizeAssistantStatus({ used_this_hour: -2, max_attachments: -1 });
    expect(clamped.used_this_hour).toBe(0);
    expect(clamped.max_attachments).toBe(0);
  });

  it("treats a blank or non-string label as absent", () => {
    const status = normalizeAssistantStatus({ model_label: "   ", tier: 7 });
    expect(status.model_label).toBeUndefined();
    expect(status.tier).toBeUndefined();
  });
});

describe("normalizeAssistantConversationList", () => {
  it("always yields an array", () => {
    expect(normalizeAssistantConversationList({}).conversations).toEqual([]);
    expect(normalizeAssistantConversationList(null).conversations).toEqual([]);
  });

  it("lets a conversation updated_at stay absent", () => {
    // `POST /assistant/conversations` does not return it; `""` would read as a
    // real timestamp to any date formatter.
    expect(normalizeAssistantConversation({ id: "c1", title: "Hi" }).updated_at).toBeUndefined();
    expect(normalizeAssistantConversation({ id: "c1", updated_at: "2026-01-01" }).updated_at).toBe(
      "2026-01-01",
    );
  });

  it("keeps a well-formed list unchanged", () => {
    const conversations = [
      {
        id: "c1",
        title: "Summaries",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-02T00:00:00Z",
      },
    ];
    expect(normalizeAssistantConversationList({ conversations }).conversations).toEqual(
      conversations,
    );
  });
});

describe("normalizeAssistantConversationDetail", () => {
  it("survives a body missing both keys", () => {
    const detail = normalizeAssistantConversationDetail({});
    expect(detail.messages).toEqual([]);
    expect(detail.conversation.id).toBe("");
  });

  it("coerces an unrecognised role to assistant", () => {
    expect(normalizeAssistantMessage({ role: "system", content: "x" }).role).toBe("assistant");
    expect(normalizeAssistantMessage({ role: "user", content: "x" }).role).toBe("user");
  });

  it("passes a well-formed body through unchanged", () => {
    const body = {
      conversation: { id: "c1", title: "T", created_at: "2026-01-01", updated_at: "2026-01-02" },
      messages: [
        { id: "m1", role: "user", content: "hi", tool_name: null, meta: null, created_at: "2026-01-01" },
        { id: "m2", role: "assistant", content: "hello" },
      ],
    };
    const detail = normalizeAssistantConversationDetail(body);
    expect(detail.conversation).toEqual(body.conversation);
    expect(detail.messages[0]).toEqual(body.messages[0]);
    expect(detail.messages[1].content).toBe("hello");
  });
});

describe("normalizeAssistantMessage — persisted meta", () => {
  it("is lossless for a legacy row (no new keys appear)", () => {
    // The important half: a row written before the richer meta existed must
    // normalise to exactly the shape it always did, so nothing downstream has
    // to tell "absent" apart from "empty".
    expect(normalizeAssistantMessage({ id: "m2", role: "assistant", content: "hello" })).toEqual({
      id: "m2",
      role: "assistant",
      content: "hello",
      tool_name: null,
      meta: null,
    });
    const legacyTool = normalizeAssistantMessage({
      id: "t2",
      role: "tool",
      content: '{"ok":true}',
      tool_name: "list_files",
      meta: { tool_call_id: "c2" },
    });
    expect(legacyTool.label).toBeUndefined();
    expect(legacyTool.summary).toBeUndefined();
    expect(legacyTool.artifacts).toBeUndefined();
  });

  it("lifts label, summary and artifacts out of meta losslessly", () => {
    const row = {
      id: "t1",
      role: "tool",
      content: '{"matches":[]}',
      tool_name: "list_files",
      meta: {
        tool_call_id: "c1",
        label: "Looking through your files",
        summary: "Found 1 file",
        artifacts: [{ type: "file", id: "f1", name: "report.pdf", meta: {} }],
      },
    };
    const out = normalizeAssistantMessage(row);
    expect(out.label).toBe("Looking through your files");
    expect(out.summary).toBe("Found 1 file");
    expect(out.artifacts).toEqual([{ type: "file", id: "f1", name: "report.pdf", meta: {} }]);
    // `meta` itself is untouched, so nothing that still reads the bag breaks.
    expect(out.meta).toEqual(row.meta);
  });

  it("reads artifacts off a final assistant message too", () => {
    const out = normalizeAssistantMessage({
      id: "a1",
      role: "assistant",
      content: "Here you go.",
      meta: { artifacts: [{ type: "job", id: "j1", name: "convert" }] },
    });
    expect(out.artifacts).toEqual([{ type: "job", id: "j1", name: "convert", meta: null }]);
    expect(out.label).toBeUndefined();
  });

  it("ignores a malformed meta rather than throwing", () => {
    const out = normalizeAssistantMessage({
      id: "t3",
      role: "tool",
      content: "{}",
      meta: { label: 7, summary: ["x"], artifacts: "not a list" },
    });
    expect(out.label).toBeUndefined();
    expect(out.summary).toBeUndefined();
    expect(out.artifacts).toBeUndefined();
  });

  it("drops artifact entries that name nothing", () => {
    const out = normalizeAssistantMessage({
      id: "t4",
      role: "tool",
      content: "{}",
      meta: { artifacts: [null, "x", {}, { type: "file", id: "f9", name: "a.pdf" }] },
    });
    expect(out.artifacts).toEqual([{ type: "file", id: "f9", name: "a.pdf", meta: null }]);
  });

  it("tolerates a non-object meta", () => {
    const out = normalizeAssistantMessage({
      id: "t5",
      role: "tool",
      content: "{}",
      meta: "nope",
    });
    expect(out.meta).toBeNull();
    expect(out.artifacts).toBeUndefined();
  });

  it("lifts meta.attachments off a user row losslessly", () => {
    // The backend echoes the turn's attachments on the user message so a
    // REVISITED conversation renders its chips.
    const out = normalizeAssistantMessage({
      id: "u1",
      role: "user",
      content: "convert this",
      meta: {
        attachments: [
          { id: "f1", name: "resume.pdf", extension: "pdf" },
          { id: "f2", name: "notes.docx" },
        ],
      },
    });
    expect(out.attachments).toEqual([
      { id: "f1", name: "resume.pdf", extension: "pdf" },
      { id: "f2", name: "notes.docx" },
    ]);
  });

  it("drops malformed attachments and keeps the message usable", () => {
    const out = normalizeAssistantMessage({
      id: "u2",
      role: "user",
      content: "hi",
      meta: {
        attachments: [null, "x", { name: "nameless.pdf" }, { id: "f3", name: 9 }, { id: "ok" }],
      },
    });
    // Only entries with an id survive; a missing name stays an empty string so
    // the chip can fall back to the id.
    expect(out.attachments).toEqual([{ id: "f3", name: "" }, { id: "ok", name: "" }]);
  });

  it("omits attachments when meta has none (legacy row unchanged)", () => {
    const out = normalizeAssistantMessage({ id: "u3", role: "user", content: "hi" });
    expect(out.attachments).toBeUndefined();
    expect("attachments" in out).toBe(false);
  });
});

describe("normalizeAssistantSummary", () => {
  it("forces key_points to a string array", () => {
    expect(normalizeAssistantSummary({ key_points: "not a list" }).key_points).toEqual([]);
    expect(normalizeAssistantSummary({ key_points: ["a", 2, null] }).key_points).toEqual(["a"]);
  });

  it("passes a well-formed body through unchanged", () => {
    const body = {
      file_id: "f1",
      file_name: "report.pdf",
      summary: "A short report.",
      key_points: ["one", "two"],
      model: "gpt-4o-mini",
    };
    expect(normalizeAssistantSummary(body)).toEqual(body);
  });
});

describe("normalizeAssistantRecommend", () => {
  it("survives a body with no recommendations", () => {
    expect(normalizeAssistantRecommend({ recommendations: null }).recommendations).toEqual([]);
  });

  it("clamps confidence into 0..1 and drops a target-less entry", () => {
    const res = normalizeAssistantRecommend({
      recommendations: [
        { target_format: "DOCX", label: "DOCX", confidence: 7 },
        { target_format: "PDF", confidence: -3 },
        { label: "No format" },
      ],
    });
    expect(res.recommendations.map((r) => r.target_format)).toEqual(["docx", "pdf"]);
    expect(res.recommendations.map((r) => r.confidence)).toEqual([1, 0]);
  });

  it("passes well-formed recommendations through unchanged", () => {
    const body = {
      source_format: "pdf",
      use_case: "resume",
      recommendations: [
        {
          target_format: "docx",
          label: "DOCX",
          category: "document",
          reason: "Editable",
          confidence: 0.82,
        },
      ],
    };
    expect(normalizeAssistantRecommend(body)).toEqual(body);
  });
});

describe("normalizeAssistantArtifact", () => {
  it("degrades an unknown type rather than guessing", () => {
    expect(normalizeAssistantArtifact({ type: "spreadsheet", id: "x" }).type).toBe("unknown");
    expect(normalizeAssistantArtifact({ type: "job", id: "j1", name: "n" }).type).toBe("job");
  });

  it("keeps a folder artifact and its meta intact", () => {
    expect(normalizeAssistantArtifact({ type: "folder", id: "f1", name: "Invoices" })).toEqual({
      type: "folder",
      id: "f1",
      name: "Invoices",
      meta: null,
    });
  });

  it("keeps meta only when it is a plain object", () => {
    expect(normalizeAssistantArtifact({ id: "x" }).meta).toBeNull();
    expect(normalizeAssistantArtifact({ id: "x", meta: ["a"] }).meta).toBeNull();
    expect(normalizeAssistantArtifact({ id: "x", meta: { status: "COMPLETED" } }).meta).toEqual({
      status: "COMPLETED",
    });
  });

  it("passes a delete artifact and its meta through untouched", () => {
    // The one type whose meta is load-bearing: dropping it (or folding the type
    // to "unknown") would leave a pending deletion with no card and no buttons.
    const artifact = normalizeAssistantArtifact({
      type: "delete",
      id: "f1",
      name: "report.pdf",
      meta: { state: "pending", conversation_id: "c1", extension: "pdf", size_bytes: 10 },
    });
    expect(artifact.type).toBe("delete");
    expect(artifact.meta).toEqual({
      state: "pending",
      conversation_id: "c1",
      extension: "pdf",
      size_bytes: 10,
    });
  });

  it("survives a malformed delete artifact without inventing fields", () => {
    const artifact = normalizeAssistantArtifact({ type: "delete", meta: "nope" });
    expect(artifact).toEqual({ type: "delete", id: "", name: "", meta: null });
  });
});

describe("normalizeAssistantDeletion", () => {
  it("keeps the two success outcomes", () => {
    expect(
      normalizeAssistantDeletion({ file_id: "f1", file_name: "a.pdf", state: "deleted" }),
    ).toEqual({ file_id: "f1", file_name: "a.pdf", state: "deleted" });
    expect(
      normalizeAssistantDeletion({ file_id: "f1", file_name: "a.pdf", state: "cancelled" }).state,
    ).toBe("cancelled");
  });

  it("degrades an unrecognised state to failed, never to deleted", () => {
    // Claiming success on an unreadable state would tell the user a file is
    // gone when it may not be.
    expect(normalizeAssistantDeletion({ state: "weird" }).state).toBe("failed");
    expect(normalizeAssistantDeletion({}).state).toBe("failed");
    expect(normalizeAssistantDeletion(null).state).toBe("failed");
  });
});

describe("normalizeAssistantStreamEvent", () => {
  it("parses each frame of the frozen contract", () => {
    expect(normalizeAssistantStreamEvent("status", '{"stage":"thinking"}')).toEqual({
      type: "status",
      stage: "thinking",
    });
    expect(normalizeAssistantStreamEvent("delta", '{"text":"hi"}')).toEqual({
      type: "delta",
      text: "hi",
    });
    expect(
      normalizeAssistantStreamEvent("tool", '{"name":"search","label":"Searching","status":"running"}'),
    ).toEqual({
      type: "tool",
      tool: { name: "search", label: "Searching", status: "running" },
    });
    expect(
      normalizeAssistantStreamEvent(
        "tool",
        '{"name":"search","status":"done","summary":"3 hits","artifacts":[{"type":"file","id":"f1","name":"a.pdf"}]}',
      ),
    ).toEqual({
      type: "tool",
      tool: {
        name: "search",
        status: "done",
        summary: "3 hits",
        artifacts: [{ type: "file", id: "f1", name: "a.pdf", meta: null }],
      },
    });
    expect(
      normalizeAssistantStreamEvent("artifact", '{"type":"job","id":"j1","name":"conv"}'),
    ).toEqual({
      type: "artifact",
      artifact: { type: "job", id: "j1", name: "conv", meta: null },
    });
    expect(
      normalizeAssistantStreamEvent(
        "done",
        '{"conversation_id":"c1","message_id":"m1","content":"answer","artifacts":[]}',
      ),
    ).toEqual({
      type: "done",
      conversation_id: "c1",
      message_id: "m1",
      content: "answer",
      artifacts: [],
    });
    expect(normalizeAssistantStreamEvent("error", '{"code":"QUOTA_EXCEEDED","message":"slow down"}')).toEqual(
      { type: "error", code: "QUOTA_EXCEEDED", message: "slow down" },
    );
  });

  it("drops an unknown event name instead of throwing", () => {
    expect(normalizeAssistantStreamEvent("telemetry", "{}")).toBeNull();
  });

  it("drops a tool frame with no name", () => {
    expect(normalizeAssistantStreamEvent("tool", '{"status":"running"}')).toBeNull();
  });

  it("degrades malformed JSON to an empty payload per event", () => {
    expect(normalizeAssistantStreamEvent("delta", "{not json")).toEqual({ type: "delta", text: "" });
    // An unparseable error still says something rather than blanking the alert.
    expect(normalizeAssistantStreamEvent("error", "boom")?.type).toBe("error");
    const error = normalizeAssistantStreamEvent("error", "boom");
    expect(error && error.type === "error" ? error.message : "").toBe("boom");
  });

  it("defaults an error code to INTERNAL_ERROR", () => {
    const event = normalizeAssistantStreamEvent("error", '{"message":"x"}');
    expect(event && event.type === "error" ? event.code : "").toBe("INTERNAL_ERROR");
  });

  it("accepts an already-parsed object payload", () => {
    expect(normalizeAssistantStreamEvent("delta", { text: "obj" })).toEqual({
      type: "delta",
      text: "obj",
    });
  });

  it("carries the optional user_message_id on a done frame", () => {
    expect(
      normalizeAssistantStreamEvent(
        "done",
        '{"conversation_id":"c1","message_id":"m1","content":"answer","artifacts":[],"user_message_id":"u1"}',
      ),
    ).toEqual({
      type: "done",
      conversation_id: "c1",
      message_id: "m1",
      content: "answer",
      artifacts: [],
      user_message_id: "u1",
    });
  });

  it("leaves user_message_id absent when the API does not send one", () => {
    const event = normalizeAssistantStreamEvent(
      "done",
      '{"conversation_id":"c1","message_id":"m1","content":"answer"}',
    );
    expect(event && event.type === "done" ? event.user_message_id : "missing").toBeUndefined();
  });

  it("normalises an unknown tool status to running rather than dropping it", () => {
    const event = normalizeAssistantStreamEvent("tool", '{"name":"t","status":"weird"}');
    expect(event && event.type === "tool" ? event.tool.status : "").toBe("unknown");
  });

  it("carries a delete artifact frame with its meta", () => {
    const event = normalizeAssistantStreamEvent(
      "artifact",
      '{"type":"delete","id":"f1","name":"report.pdf","meta":{"state":"pending","conversation_id":"c1"}}',
    );
    expect(event).toEqual({
      type: "artifact",
      artifact: {
        type: "delete",
        id: "f1",
        name: "report.pdf",
        meta: { state: "pending", conversation_id: "c1" },
      },
    });
  });
});

describe("wallet split, plan change, payment method session", () => {
  it("asNullableBoolean keeps three states", () => {
    // `null` is a real answer ("the API did not say"), not a false default.
    expect(asNullableBoolean(true)).toBe(true);
    expect(asNullableBoolean(false)).toBe(false);
    expect(asNullableBoolean(undefined)).toBeNull();
    expect(asNullableBoolean("true")).toBeNull();
  });

  it("normalizeCreditBalance leaves an older API's missing split as null", () => {
    // The whole wallet block is additive: an older API sends none of it, and
    // every field must read as "not said" rather than a fabricated 0.
    const b = normalizeCreditBalance({ balance: 12, tier: "PRO" });
    expect(b.balance).toBe(12);
    expect(b.plan_remaining).toBeNull();
    expect(b.carryover_credits).toBeNull();
    expect(b.carryover_expires_at).toBeNull();
    expect(b.purchased_credits).toBeNull();
    expect(b.purchased_credits_first).toBeNull();
    expect(b.total_available).toBeNull();
  });

  it("normalizeCreditBalance preserves a real split", () => {
    const b = normalizeCreditBalance({
      balance: 120,
      tier: "PRO_PLUS",
      plan_remaining: 120,
      carryover_credits: 320,
      carryover_expires_at: "2026-11-01T00:00:00Z",
      purchased_credits: 1000,
      purchased_credits_first: true,
      total_available: 1440,
    });
    expect(b.plan_remaining).toBe(120);
    expect(b.carryover_credits).toBe(320);
    expect(b.carryover_expires_at).toBe("2026-11-01T00:00:00Z");
    expect(b.purchased_credits).toBe(1000);
    expect(b.purchased_credits_first).toBe(true);
    expect(b.total_available).toBe(1440);
  });

  it("normalizeCreditBalance rejects a non-numeric or non-boolean split field", () => {
    const b = normalizeCreditBalance({
      carryover_credits: "320",
      purchased_credits_first: "yes",
      total_available: {},
    });
    expect(b.carryover_credits).toBeNull();
    expect(b.purchased_credits_first).toBeNull();
    expect(b.total_available).toBeNull();
  });

  it("normalizeChangePlan defaults every field of a malformed body", () => {
    // The page confirms the change FROM this object, so a missing count must be
    // 0 (nothing carried over) and never the string "undefined".
    expect(normalizeChangePlan({})).toEqual({
      tier: "",
      previous_tier: "",
      plan_credits: 0,
      carryover_credits: 0,
      carryover_expires_at: null,
      scheduled_effective_at: null,
      message: "",
    });
  });

  it("normalizeChangePlan preserves a real upgrade", () => {
    const r = normalizeChangePlan({
      tier: "PRO_PLUS",
      previous_tier: "PRO",
      plan_credits: 2000,
      carryover_credits: 320,
      carryover_expires_at: "2026-11-01T00:00:00Z",
      scheduled_effective_at: null,
      message: "Upgrade applied.",
    });
    expect(r.tier).toBe("PRO_PLUS");
    expect(r.previous_tier).toBe("PRO");
    expect(r.plan_credits).toBe(2000);
    expect(r.carryover_credits).toBe(320);
    expect(r.carryover_expires_at).toBe("2026-11-01T00:00:00Z");
    expect(r.scheduled_effective_at).toBeNull();
    expect(r.message).toBe("Upgrade applied.");
  });

  it("normalizePaymentMethodSession defaults a malformed body to not enabled", () => {
    // Defaulting to `true` (the schema's own default) would make the page try
    // to mount the Payment Element with no secret, which renders blank.
    expect(normalizePaymentMethodSession({})).toEqual({
      client_secret: null,
      setup_intent_client_secret: null,
      enabled: false,
    });
    expect(normalizePaymentMethodSession({ enabled: "true" }).enabled).toBe(false);
    expect(normalizePaymentMethodSession({ client_secret: 42 }).client_secret).toBeNull();
  });

  it("normalizePaymentMethodSession preserves a real session", () => {
    expect(
      normalizePaymentMethodSession({
        client_secret: "cs_test_abc",
        setup_intent_client_secret: "seti_secret_abc",
        enabled: true,
      }),
    ).toEqual({
      client_secret: "cs_test_abc",
      setup_intent_client_secret: "seti_secret_abc",
      enabled: true,
    });
    expect(
      normalizePaymentMethodSession({ client_secret: null, enabled: false }),
    ).toEqual({ client_secret: null, setup_intent_client_secret: null, enabled: false });
    // An older API omits the SetupIntent entirely; `null` makes the section
    // fall back to the Element's deferred mode rather than fail.
    expect(
      normalizePaymentMethodSession({ client_secret: "cs_test_abc", enabled: true })
        .setup_intent_client_secret,
    ).toBeNull();
  });

  it("normalizePaymentMethodList degrades a malformed body to no cards, not undefined", () => {
    // The card list renders `methods.length` and `methods.map`, so an absent
    // array used to be a whole-page crash rather than an empty section.
    expect(normalizePaymentMethodList({})).toEqual({ methods: [], enabled: false });
    expect(normalizePaymentMethodList(null).methods).toEqual([]);
    expect(normalizePaymentMethodList({ methods: "nope" }).methods).toEqual([]);
    // A malformed body cannot be trusted as an enabled card list, so it reads
    // as "no card UI" rather than an empty-but-live list.
    expect(normalizePaymentMethodList({ enabled: "true" }).enabled).toBe(false);
  });

  it("normalizePaymentMethodList coerces each card and ignores unknown fields", () => {
    const list = normalizePaymentMethodList({
      enabled: true,
      methods: [
        {
          id: "pm_1",
          brand: "visa",
          last4: "4242",
          exp_month: 4,
          exp_year: 2032,
          is_default: true,
          // The API sends the wallet as a plain string. A shape it does not
          // use reads as "no wallet" rather than leaking an object into the UI.
          wallet: "apple_pay",
          // A field this SPA does not know about yet must not crash the list.
          future_field: { anything: true },
        },
        // A half-populated row still renders rather than throwing.
        { id: "pm_2", exp_month: "not-a-number" },
      ],
    });

    expect(list.enabled).toBe(true);
    expect(list.methods[0]).toEqual({
      id: "pm_1",
      brand: "visa",
      last4: "4242",
      exp_month: 4,
      exp_year: 2032,
      is_default: true,
      wallet: "apple_pay",
    });
    expect(list.methods[1]).toEqual({
      id: "pm_2",
      brand: "",
      last4: "",
      exp_month: 0,
      exp_year: 0,
      // Absent `is_default` reads as "not the default", never `undefined`.
      is_default: false,
      // A hand-keyed card has no wallet, and that must stay null.
      wallet: null,
    });
  });

  it("normalizePaymentMethodList drops a non-string wallet instead of rendering it", () => {
    const list = normalizePaymentMethodList({
      enabled: true,
      methods: [{ id: "pm_1", wallet: { type: "apple_pay" } }],
    });

    expect(list.methods[0].wallet).toBeNull();
  });
});

describe("normalizeConnectedAppList", () => {
  it("coerces every field so the card can render a malformed body", () => {
    const list = normalizeConnectedAppList({
      apps: [{ id: "g1", client_name: null, scopes: "documents.read" }],
    });

    expect(list.apps[0].id).toBe("g1");
    // `scopes` is mapped over to render badges, so it must always be an array.
    expect(list.apps[0].scopes).toEqual([]);
    expect(list.apps[0].client_name).toBe("");
    expect(list.apps[0].last_used_at).toBeNull();
  });

  it("returns an empty list rather than throwing on a non-object body", () => {
    expect(normalizeConnectedAppList(null).apps).toEqual([]);
    expect(normalizeConnectedAppList({ apps: "nope" }).apps).toEqual([]);
  });

  it("carries the confinement fields, defaulting an older API to whole-Drive", () => {
    const list = normalizeConnectedAppList({
      apps: [
        {
          id: "g1",
          folder_access: "FOLDER",
          folder_id: "f1",
          folder_name: "Reports",
          history_scope: "ALL",
        },
        { id: "g2" },
      ],
    });

    expect(list.apps[0].folder_access).toBe("FOLDER");
    expect(list.apps[0].folder_id).toBe("f1");
    expect(list.apps[0].folder_name).toBe("Reports");
    expect(list.apps[0].history_scope).toBe("ALL");
    // An API older than folder scoping sends neither field; reading it as FOLDER
    // would trap the user on a picker that API never populated.
    expect(list.apps[1].folder_access).toBe("ALL");
    expect(list.apps[1].folder_id).toBeNull();
    expect(list.apps[1].folder_name).toBeNull();
    expect(list.apps[1].history_scope).toBe("AGENT");
  });
});

describe("normalizeMcpConsent", () => {
  it("keeps the requested flags and coerces missing ones to false", () => {
    const consent = normalizeMcpConsent({
      client_id: "c1",
      client_name: "Agent",
      redirect_uri: "https://agent.test/cb",
      resource: "https://api.test/mcp",
      scopes: [
        { scope: "documents.read", description: "Read", requested: true },
        { scope: "documents.delete", description: "Delete" },
      ],
    });

    expect(consent.client_name).toBe("Agent");
    expect(consent.scopes[0].requested).toBe(true);
    // Only an explicit `true` may count as requested: a malformed flag must
    // never make a permission approvable.
    expect(consent.scopes[1].requested).toBe(false);
    expect(consent.scopes[1].already_granted).toBe(false);
  });

  it("keeps a confined binding and the folder list", () => {
    const consent = normalizeMcpConsent({
      folder_access: "FOLDER",
      folder_id: "f1",
      folders: [{ folder_id: "f1", name: "Reports" }],
      history_scope: "ALL",
      can_choose_history_scope: true,
    });

    expect(consent.folder_access).toBe("FOLDER");
    expect(consent.folder_id).toBe("f1");
    expect(consent.folders).toEqual([{ folder_id: "f1", name: "Reports" }]);
    expect(consent.history_scope).toBe("ALL");
    expect(consent.can_choose_history_scope).toBe(true);
  });

  it("reads an absent folder field as whole-Drive rather than trapping the user", () => {
    // An API older than folder scoping sends neither field. Reading it as
    // FOLDER would render a picker whose folder_id that API never sent.
    const consent = normalizeMcpConsent({});
    expect(consent.folder_access).toBe("ALL");
    expect(consent.folder_id).toBeNull();
    expect(consent.folders).toEqual([]);
  });

  it("fails a present-but-unrecognised folder value closed to FOLDER", () => {
    // A value the API did send but we do not understand is a real risk of
    // widening access, so it reads as the least-privilege option.
    expect(normalizeMcpConsent({ folder_access: "SOMETHING_NEW" }).folder_access).toBe("FOLDER");
  });

  it("defaults the history scope to AGENT and the choice to unavailable", () => {
    const consent = normalizeMcpConsent({});
    expect(consent.history_scope).toBe("AGENT");
    expect(consent.can_choose_history_scope).toBe(false);
  });
});

describe("normalizeMcpFolderAccess", () => {
  it("keeps well-formed entries", () => {
    const res = normalizeMcpFolderAccess({
      folders: [{ folder_id: "f1", client_name: "Claude Desktop", grant_id: "g1" }],
    });
    expect(res.folders).toEqual([
      { folder_id: "f1", client_name: "Claude Desktop", grant_id: "g1" },
    ]);
  });

  it("degrades a missing body to no folders instead of throwing", () => {
    // This backs a quiet decoration on the Files page; it must never take the
    // page down.
    expect(normalizeMcpFolderAccess(undefined).folders).toEqual([]);
  });
});

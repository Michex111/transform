/**
 * Runtime shape guards for API responses.
 *
 * The client's declared response types describe what the API is *supposed* to
 * return, but nothing enforces that at runtime: a `200` whose body is missing a
 * key is still cast to that type, and every consumer then assumes the shape
 * holds. That turned a malformed response into a whole-page crash rather than
 * degraded UI:
 *
 *   - `setConversionMap(res.conversions)` → `undefined`, then `Object.keys(undefined)`
 *   - `stats?.conversion_stats.total_jobs` → `?.` guards only `stats`
 *   - `plan.features.map(...)`, `keys.length`, `files.map(...)`, `body.jobs`
 *
 * Normalising at the client boundary makes the declared types true, so a
 * malformed response degrades to an empty/default value instead of taking the
 * page down. Every normaliser is lossless for a well-formed payload — it only
 * substitutes defaults where the field is absent or the wrong primitive type.
 *
 * Scope: collection shapes (the arrays consumers iterate) and the nested
 * objects they dereference. Individual item fields are coerced only where a
 * nested array is iterated (`features`, `breakdown`) — a partially-populated
 * item renders as blank text, which is not a crash.
 */

import type {
  APIKeyCreateResponse,
  APIKeyListResponse,
  BatchDeleteResponse,
  CancelSubscriptionResponse,
  CheckoutResponse,
  ConversionHistoryResponse,
  ConversionJobResponse,
  ConversionMapResponse,
  ConversionStats,
  CreditBalanceResponse,
  CreditPricingResponse,
  CreditTransactionResponse,
  DashboardResponse,
  DeleteHistoryPreviewResponse,
  DeleteHistoryRangeResponse,
  FileDownloadResponse,
  FileListResponse,
  FileMetadataResponse,
  FolderContentsResponse,
  FolderListResponse,
  FolderResponse,
  ForgotPasswordResponse,
  GuestJobResponse,
  HistoryDeleteRange,
  PhoneVerificationStatusResponse,
  PortalResponse,
  PresignedUrlResponse,
  ResendVerificationResponse,
  ResetPasswordResponse,
  StorageBreakdownEntry,
  StorageStats,
  SubscriptionPlanResponse,
  SubscriptionStatusResponse,
  SupportedConversion,
  TokenResponse,
  UploadResponse,
  UploadSession,
  UploadSessionPartsResponse,
  UserResponse,
  VerifyEmailResponse,
} from "./types"
import { HISTORY_DELETE_RANGES } from "./types"

/* ------------------------------------------------------------------ *
 * Primitives
 * ------------------------------------------------------------------ */

/** `value` when it is an array, otherwise `[]`. */
export function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : []
}

/** An array of strings, dropping any non-string entries. */
export function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : []
}

export function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback
}

export function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback
}

export function asNullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null
}

export function asNullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

export function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback
}

/** A plain-object view of `value`; `{}` for null, arrays, and primitives. */
export function asObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/* ------------------------------------------------------------------ *
 * Auth
 * ------------------------------------------------------------------ */

export function normalizeUser(value: unknown): UserResponse {
  const o = asObject(value)
  return {
    id: asNumber(o.id),
    username: asString(o.username),
    email: asString(o.email),
    is_active: asBoolean(o.is_active),
    created_at: asString(o.created_at),
    // An older API omits this field entirely. Defaulting to `false` would flag
    // every account as unverified against that API, with no way for the user to
    // clear it; `true` degrades silently instead.
    email_verified: asBoolean(o.email_verified, true),
    // Profile fields. `null` (not `""`) is the "absent" value for the nullable
    // ones so `user.first_name === null` and "the API is older" are
    // indistinguishable — which is what we want: both render as "no name set".
    first_name: asNullableString(o.first_name),
    last_name: asNullableString(o.last_name),
    // Derived server-side. Left as an empty string when absent so the callers
    // in `lib/avatar.ts` can fall back to the username in one place.
    display_name: asString(o.display_name),
    initials: asString(o.initials),
    avatar_url: asNullableString(o.avatar_url),
    phone_number: asNullableString(o.phone_number),
    // Defaults to `false`, unlike `email_verified`: an unverified phone is the
    // normal state and cannot be "wrongly" shown, whereas claiming a number is
    // verified when the API never said so would be a lie about a security
    // control. The section simply reads as "not verified" against an old API.
    phone_verified: asBoolean(o.phone_verified, false),
    // The `|| null` is the point: `asNullableString` keeps `""` as `""`, but an
    // empty id is the API's "cleared" value and is not a folder. Missing, `""`
    // and a malformed non-string must all arrive here as `null` — the single
    // "no preference" value — so an API too old to send this field degrades to
    // "save to the drive root" instead of a save against an empty folder id.
    default_save_folder_id: asNullableString(o.default_save_folder_id) || null,
  }
}

/**
 * Coerce a `range` echo to a known window.
 *
 * Display-only: the caller always knows which range it asked for, so a
 * malformed echo must not throw or produce `"undefined"` in a confirmation.
 */
export function asHistoryDeleteRange(
  value: unknown,
  fallback: HistoryDeleteRange = "24h",
): HistoryDeleteRange {
  return typeof value === "string" && (HISTORY_DELETE_RANGES as readonly string[]).includes(value)
    ? (value as HistoryDeleteRange)
    : fallback
}

export function normalizePhoneStatus(value: unknown): PhoneVerificationStatusResponse {
  const o = asObject(value)
  return {
    phone_number: asNullableString(o.phone_number),
    phone_verified: asBoolean(o.phone_verified, false),
    expires_in_seconds: asNullableNumber(o.expires_in_seconds),
    resend_available_in_seconds: asNullableNumber(o.resend_available_in_seconds),
  }
}

export function normalizeDeleteHistoryPreview(value: unknown): DeleteHistoryPreviewResponse {
  const o = asObject(value)
  return {
    range: asHistoryDeleteRange(o.range),
    since: asNullableString(o.since),
    count: asNumber(o.count),
    // Defaults to 0 rather than erroring: a missing count only makes the
    // warning line disappear, and the delete itself is still scoped.
    active_count: asNumber(o.active_count),
  }
}

export function normalizeDeleteHistoryRange(value: unknown): DeleteHistoryRangeResponse {
  const o = asObject(value)
  return {
    deleted_count: asNumber(o.deleted_count),
    skipped_active: asNumber(o.skipped_active),
    range: asHistoryDeleteRange(o.range),
  }
}

export function normalizeVerifyEmail(value: unknown): VerifyEmailResponse {
  const o = asObject(value)
  return {
    ok: asBoolean(o.ok),
    already_verified: asBoolean(o.already_verified),
    username: asNullableString(o.username),
    message: asString(o.message),
  }
}

export function normalizeResendVerification(value: unknown): ResendVerificationResponse {
  return { message: asString(asObject(value).message) }
}

/**
 * The confirmation shown after a reset request. Answers 202 with the same body
 * for every address, so all this can do is keep the message a string — and an
 * empty one must render as the page's own copy, not as `undefined`.
 */
export function normalizeForgotPassword(value: unknown): ForgotPasswordResponse {
  return { message: asString(asObject(value).message) }
}

/**
 * `username` stays nullable rather than being coerced to `""`: the sign-in form
 * pre-fills from it, and an empty string would submit as a pre-filled blank.
 */
export function normalizeResetPassword(value: unknown): ResetPasswordResponse {
  const o = asObject(value)
  return {
    ok: asBoolean(o.ok),
    username: asNullableString(o.username),
    message: asString(o.message),
  }
}

/* ------------------------------------------------------------------ *
 * Dashboard
 * ------------------------------------------------------------------ */

export function normalizeConversionStats(value: unknown): ConversionStats {
  const o = asObject(value)
  return {
    total_jobs: asNumber(o.total_jobs),
    successful_jobs: asNumber(o.successful_jobs),
    failed_jobs: asNumber(o.failed_jobs),
    total_credits_used: asNumber(o.total_credits_used),
  }
}

export function normalizeStorageStats(value: unknown): StorageStats {
  const o = asObject(value)
  // `breakdown` is additive; a non-array becomes `[]`, which every consumer
  // already treats as "no breakdown data" (so the plain bar is used).
  const breakdown: StorageBreakdownEntry[] = asArray<unknown>(o.breakdown).map((row) => {
    const r = asObject(row)
    return {
      extension: asString(r.extension),
      bytes: asNumber(r.bytes),
      file_count: asNumber(r.file_count),
    }
  })
  return {
    used_bytes: asNumber(o.used_bytes),
    limit_bytes: asNumber(o.limit_bytes),
    used_percent: asNumber(o.used_percent),
    file_count: asNumber(o.file_count),
    breakdown,
    // Additive fields: `null` (not `0`) is the "the API did not say" value, so
    // a caller can tell "no quota left" apart from "this API is older" and fall
    // back to the server's 413 instead of refusing every file against a
    // fabricated zero.
    available_bytes: asNullableNumber(o.available_bytes),
    max_file_size_bytes: asNullableNumber(o.max_file_size_bytes),
  }
}

/**
 * Guarantees the nested `conversion_stats` / `storage_stats` objects exist, so
 * `stats?.conversion_stats.total_jobs` cannot throw: `?.` only guards `stats`,
 * not the property read that follows it.
 */
export function normalizeDashboard(value: unknown): DashboardResponse {
  const o = asObject(value)
  return {
    conversion_stats: normalizeConversionStats(o.conversion_stats),
    storage_stats: normalizeStorageStats(o.storage_stats),
    credit_balance: asNumber(o.credit_balance),
    tier: asString(o.tier),
    recent_jobs_count: asNumber(o.recent_jobs_count),
    active_api_keys: asNumber(o.active_api_keys),
    credits_reset_at: asNullableString(o.credits_reset_at),
  }
}

/* ------------------------------------------------------------------ *
 * Conversions
 * ------------------------------------------------------------------ */

export function normalizeSupportedConversions(value: unknown): SupportedConversion[] {
  return asArray<unknown>(value).map((row) => {
    const r = asObject(row)
    return {
      source_format: asString(r.source_format),
      target_format: asString(r.target_format),
    }
  })
}

/**
 * Always returns a `conversions` object, so `Object.keys(map)` and
 * `map[format]` are safe when the key is absent.
 */
export function normalizeConversionMap(value: unknown): ConversionMapResponse {
  const raw = asObject(asObject(value).conversions)
  const conversions: Record<string, string[]> = {}
  for (const [source, targets] of Object.entries(raw)) {
    conversions[source] = asStringArray(targets)
  }
  return { conversions }
}

export function normalizeConversionJob(value: unknown): ConversionJobResponse {
  const o = asObject(value)
  return {
    job_id: asString(o.job_id),
    status: asString(o.status),
    source_format: asString(o.source_format),
    target_format: asString(o.target_format),
    input_file: asString(o.input_file),
    output_file: asNullableString(o.output_file),
    object_key: asNullableString(o.object_key),
    download_url: asNullableString(o.download_url),
    error_message: asNullableString(o.error_message),
    credits_used: asNumber(o.credits_used),
    compute_duration_ms: asNumber(o.compute_duration_ms),
    input_size_bytes: asNumber(o.input_size_bytes),
    output_size_bytes: asNumber(o.output_size_bytes),
    created_at: asNullableString(o.created_at),
    data_key_wrapped: asNullableString(o.data_key_wrapped),
    client_encrypted: asBoolean(o.client_encrypted),
  }
}

export function normalizeConversionHistory(value: unknown): ConversionHistoryResponse {
  const o = asObject(value)
  return {
    jobs: asArray<unknown>(o.jobs).map(normalizeConversionJob),
    total: asNumber(o.total),
    page: asNumber(o.page, 1),
    page_size: asNumber(o.page_size, 20),
  }
}

/**
 * A guest job adds `guest_token`, which every subsequent guest request needs.
 * Coercing it to `""` (rather than leaving it `undefined`) keeps the failure a
 * clean API error instead of a request to `...?guest_token=undefined`.
 */
export function normalizeGuestJob(value: unknown): GuestJobResponse {
  return {
    ...normalizeConversionJob(value),
    guest_token: asString(asObject(value).guest_token),
  }
}

/* ------------------------------------------------------------------ *
 * Files & folders
 * ------------------------------------------------------------------ */

export function normalizeFile(value: unknown): FileMetadataResponse {
  const o = asObject(value)
  return {
    id: asString(o.id),
    file_name: asString(o.file_name),
    file_key: asString(o.file_key),
    file_size_bytes: asNumber(o.file_size_bytes),
    mime_type: asString(o.mime_type),
    folder_id: asNullableString(o.folder_id),
    is_favorite: asBoolean(o.is_favorite),
    created_at: asString(o.created_at),
    expires_at: asNullableString(o.expires_at),
  }
}

export function normalizeFolder(value: unknown): FolderResponse {
  const o = asObject(value)
  return {
    id: asString(o.id),
    name: asString(o.name),
    parent_id: asNullableString(o.parent_id),
    created_at: asString(o.created_at),
    updated_at: asString(o.updated_at),
  }
}

/** `{files: [...]}` — always an array even when the key is missing. */
export function normalizeFileList(value: unknown): FileListResponse {
  const o = asObject(value)
  return {
    files: asArray<unknown>(o.files).map(normalizeFile),
    total: asNumber(o.total),
    page: asNumber(o.page, 1),
    page_size: asNumber(o.page_size, 20),
  }
}

/** `{folders: [...]}` — always an array even when the key is missing. */
export function normalizeFolderList(value: unknown): FolderListResponse {
  const o = asObject(value)
  return {
    folders: asArray<unknown>(o.folders).map(normalizeFolder),
    total: asNumber(o.total),
    page: asNumber(o.page, 1),
    page_size: asNumber(o.page_size, 20),
  }
}

/**
 * `{folder, folders, files}` — both collections are always arrays, and `folder`
 * is always an object, so a folder-browsing screen cannot crash on a partial
 * body.
 */
export function normalizeFolderContents(value: unknown): FolderContentsResponse {
  const o = asObject(value)
  return {
    folder: normalizeFolder(o.folder),
    folders: asArray<unknown>(o.folders).map(normalizeFolder),
    files: asArray<unknown>(o.files).map(normalizeFile),
    total_folders: asNumber(o.total_folders),
    total_files: asNumber(o.total_files),
  }
}

export function normalizePresignedUrls(value: unknown): PresignedUrlResponse[] {
  return asArray<unknown>(value).map((row) => {
    const r = asObject(row)
    return {
      url: asString(r.url),
      object_key: asString(r.object_key),
      expires_in_seconds: asNumber(r.expires_in_seconds),
    }
  })
}

export function normalizeFileDownload(value: unknown): FileDownloadResponse {
  const o = asObject(value)
  return {
    download_url: asString(o.download_url),
    expires_in_seconds: asNumber(o.expires_in_seconds),
  }
}

export function normalizeBatchDelete(value: unknown): BatchDeleteResponse {
  const o = asObject(value)
  return {
    deleted_files: asNumber(o.deleted_files),
    deleted_folders: asNumber(o.deleted_folders),
  }
}

/* ------------------------------------------------------------------ *
 * Uploads
 * ------------------------------------------------------------------ */

export function normalizeUploadResponse(value: unknown): UploadResponse {
  const o = asObject(value)
  return {
    upload_id: asString(o.upload_id),
    object_key: asString(o.object_key),
    upload_url: asNullableString(o.upload_url),
    expires_in_minutes: asNumber(o.expires_in_minutes),
    // An unrecognised (or absent) mode degrades to `"single"`: that is the
    // path every API before multipart supported, and guessing `"multipart"`
    // for an unknown value would send the caller looking for part URLs a
    // single-PUT session never mints.
    upload_mode: o.upload_mode === "multipart" ? "multipart" : "single",
    part_size_bytes: asNullableNumber(o.part_size_bytes),
    part_count: asNullableNumber(o.part_count),
    max_file_size_bytes: asNullableNumber(o.max_file_size_bytes),
  }
}

export function normalizeUploadSessionParts(value: unknown): UploadSessionPartsResponse {
  const o = asObject(value)
  return {
    parts: asArray<unknown>(o.parts).map((row) => {
      const r = asObject(row)
      return { part_number: asNumber(r.part_number), url: asString(r.url) }
    }),
    expires_in_minutes: asNumber(o.expires_in_minutes),
  }
}

export function normalizeUploadSession(value: unknown): UploadSession {
  const o = asObject(value)
  return {
    upload_id: asString(o.upload_id),
    object_key: asString(o.object_key),
    status: asString(o.status),
    file_name: asNullableString(o.file_name),
    folder_id: asNullableString(o.folder_id),
  }
}

/* ------------------------------------------------------------------ *
 * Credits & subscription
 * ------------------------------------------------------------------ */

export function normalizeCreditBalance(value: unknown): CreditBalanceResponse {
  const o = asObject(value)
  return {
    balance: asNumber(o.balance),
    tier: asString(o.tier),
    monthly_allowance: asNullableNumber(o.monthly_allowance),
    monthly_remaining: asNullableNumber(o.monthly_remaining),
    credits_reset_at: asNullableString(o.credits_reset_at),
  }
}

/** Bare arrays — the credit pages iterate these directly. */
export function normalizeCreditHistory(value: unknown): CreditTransactionResponse[] {
  return asArray<unknown>(value).map((row) => {
    const r = asObject(row)
    return {
      id: asString(r.id),
      amount: asNumber(r.amount),
      transaction_type: asString(r.transaction_type),
      reference_id: asNullableString(r.reference_id),
      description: asNullableString(r.description),
      created_at: asString(r.created_at),
    }
  })
}

export function normalizeCreditPricing(value: unknown): CreditPricingResponse[] {
  return asArray<unknown>(value).map((row) => {
    const r = asObject(row)
    return {
      credits: asNumber(r.credits),
      price_usd: asNumber(r.price_usd),
      price_per_credit: asNumber(r.price_per_credit),
    }
  })
}

/** `features` is iterated by the pricing page, so it must always be an array. */
export function normalizeSubscriptionPlan(value: unknown): SubscriptionPlanResponse {
  const o = asObject(value)
  return {
    tier: asString(o.tier),
    name: asString(o.name),
    price_monthly_usd: asNullableNumber(o.price_monthly_usd),
    storage_gb: asNumber(o.storage_gb),
    monthly_credits: asNullableNumber(o.monthly_credits),
    features: asStringArray(o.features),
  }
}

export function normalizeSubscriptionPlans(value: unknown): SubscriptionPlanResponse[] {
  return asArray<unknown>(value).map(normalizeSubscriptionPlan)
}

export function normalizeSubscriptionStatus(value: unknown): SubscriptionStatusResponse {
  const o = asObject(value)
  return {
    tier: asString(o.tier),
    status: asString(o.status),
    current_period_start: asNullableString(o.current_period_start),
    current_period_end: asNullableString(o.current_period_end),
    stripe_subscription_id: asNullableString(o.stripe_subscription_id),
  }
}

/* ------------------------------------------------------------------ *
 * API keys
 * ------------------------------------------------------------------ */

/** `{keys: [...]}` — `keys.length` in Settings assumed the array always exists. */
export function normalizeApiKeyList(value: unknown): APIKeyListResponse {
  const o = asObject(value)
  return {
    keys: asArray<unknown>(o.keys).map((row) => {
      const r = asObject(row)
      return {
        id: asString(r.id),
        name: asString(r.name),
        prefix: asString(r.prefix),
        status: asString(r.status),
        created_at: asString(r.created_at),
        last_used_at: asNullableString(r.last_used_at),
        expires_at: asNullableString(r.expires_at),
      }
    }),
  }
}

export function normalizeApiKeyCreate(value: unknown): APIKeyCreateResponse {
  const o = asObject(value)
  return {
    id: asString(o.id),
    name: asString(o.name),
    key: asString(o.key),
    prefix: asString(o.prefix),
    created_at: asString(o.created_at),
    expires_at: asNullableString(o.expires_at),
    message: asString(o.message),
  }
}

/* ------------------------------------------------------------------ *
 * Auth tokens & redirect payloads
 * ------------------------------------------------------------------ */

/**
 * Coercing the token to `""` keeps a malformed body a clean
 * "unauthenticated" state instead of storing `undefined` as a bearer token.
 */
export function normalizeTokenResponse(value: unknown): TokenResponse {
  const o = asObject(value)
  return {
    access_token: asString(o.access_token),
    token_type: asString(o.token_type, "bearer"),
    refresh_token: asNullableString(o.refresh_token),
    expires_in: asNullableNumber(o.expires_in),
  }
}

/**
 * These carry a URL the client navigates to. Without normalisation a missing
 * field navigates the browser to the literal string "undefined".
 */
export function normalizeCheckout(value: unknown): CheckoutResponse {
  return { checkout_url: asString(asObject(value).checkout_url) }
}

export function normalizePortal(value: unknown): PortalResponse {
  return { portal_url: asString(asObject(value).portal_url) }
}

export function normalizeCancelSubscription(value: unknown): CancelSubscriptionResponse {
  const o = asObject(value)
  return {
    message: asString(o.message),
    tier_after_cancel: asString(o.tier_after_cancel),
  }
}

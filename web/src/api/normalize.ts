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
  FileDownloadResponse,
  FileListResponse,
  FileMetadataResponse,
  FolderContentsResponse,
  FolderListResponse,
  FolderResponse,
  GuestJobResponse,
  PortalResponse,
  PresignedUrlResponse,
  StorageBreakdownEntry,
  StorageStats,
  SubscriptionPlanResponse,
  SubscriptionStatusResponse,
  SupportedConversion,
  TokenResponse,
  UploadResponse,
  UploadSession,
  UserResponse,
} from "./types"

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
    upload_url: asString(o.upload_url),
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

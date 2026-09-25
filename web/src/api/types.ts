// ---- Auth ----
export interface UserCreateRequest {
  username: string
  email: string
  password: string
  /**
   * Optional on the wire, required in the sign-up form.
   *
   * The API keeps them optional because every account created before this
   * feature exists has no name — the columns are nullable, so a required field
   * would make the request schema reject a body the database is happy with, and
   * would break every existing API client. The SPA always sends them.
   */
  first_name?: string | null
  last_name?: string | null
}

export interface UserResponse {
  id: number
  username: string
  email: string
  is_active: boolean
  created_at: string
  /**
   * False until the address is confirmed through the emailed link.
   *
   * Optional because the API and the SPA deploy independently (auto-deploy is
   * off), so a new bundle can briefly talk to an older API that does not send
   * it. An absent value is normalised to `true` — treating "the API didn't say"
   * as "unverified" would show every user of that API a warning they cannot
   * clear. Read it through `normalizeUser`, not directly.
   */
  email_verified?: boolean

  // ---- Profile (added by the profile overhaul) ----
  //
  // Every field below is optional for the same independent-deploy reason as
  // `email_verified`: an older API omits them entirely, and the UI must degrade
  // to the username-based presentation it used before rather than render
  // `undefined`. Always read them through `normalizeUser`/the helpers in
  // `lib/avatar.ts` — never index into `user` directly.
  first_name?: string | null
  last_name?: string | null
  /** `"Ada Lovelace"` when a name is set, otherwise the username. */
  display_name?: string
  /** `"AL"` from the names, otherwise the first two letters of the username. */
  initials?: string
  /**
   * A ready-to-render `data:image/webp;base64,…` URL, or null when the account
   * has no picture.
   *
   * It is a data URL rather than a server path on purpose: an `<img>` cannot
   * carry the bearer token, so a `GET`-able avatar endpoint would have to be
   * either unauthenticated (user-id enumerable) or fetched into a blob URL.
   * Avatars are tiny after downscaling, so inlining them removes the whole
   * problem along with its caching and CORS surface.
   */
  avatar_url?: string | null
  /** E.164 (`+14155552671`), set as soon as a number is submitted — verified or not. */
  phone_number?: string | null
  phone_verified?: boolean
  /**
   * The folder "Save to Drive" files a converted output into, or null for the
   * drive root ("My Drive") when nothing is configured.
   *
   * Optional for the same independent-deploy reason as `email_verified`: an
   * older API omits it entirely, and "the API didn't say" means "no
   * preference" — save to the root — never an error. Read it through
   * `lib/saveToDrive.ts#defaultSaveFolderId`, which folds every unset shape
   * (absent, null, "", whitespace, a malformed non-string) into one `null`.
   */
  default_save_folder_id?: string | null
}

/** Editable profile fields. An empty string clears the corresponding name. */
export interface UpdateProfileRequest {
  first_name?: string | null
  last_name?: string | null
  /**
   * The default "Save to Drive" destination, deliberately tri-state:
   *   - an ABSENT key leaves the stored preference untouched, so a PATCH that
   *     only edits a name cannot clear a folder the user chose;
   *   - a folder id stores that folder (the API answers 404 `Folder not found`
   *     for an id that is unknown *or* belongs to another account);
   *   - `null` (or `""`) clears it back to the drive root.
   */
  default_save_folder_id?: string | null
}

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
}

/** Body for requesting (or changing) the number an SMS code is sent to. */
export interface RequestPhoneVerificationRequest {
  phone_number: string
}

export interface VerifyPhoneRequest {
  code: string
}

/**
 * The state of phone verification after any phone-related call.
 *
 * One shape for every outcome (requested, resent, verified, or queried) so the
 * settings UI has a single source of truth for what to render.
 */
export interface PhoneVerificationStatusResponse {
  phone_number: string | null
  phone_verified: boolean
  /** Seconds until the code that was just sent stops working. */
  expires_in_seconds?: number | null
  /**
   * Seconds until another code may be sent.
   *
   * The API suppresses a too-early resend silently (it answers 202 and sends
   * nothing) so the endpoint cannot be used to probe, so the UI must run its
   * own countdown from this value or the button will look broken.
   */
  resend_available_in_seconds?: number | null
}

/**
 * Machine-readable codes the profile/phone endpoints return in a structured
 * `detail` object. Kept here (not in the pages) so the copy that explains them
 * lives beside the contract it interprets — see `lib/phone.ts`.
 */
export const INVALID_PHONE_NUMBER = "INVALID_PHONE_NUMBER"
export const PHONE_IN_USE = "PHONE_IN_USE"
export const SMS_DELIVERY_FAILED = "SMS_DELIVERY_FAILED"
export const PHONE_CODE_INVALID = "PHONE_CODE_INVALID"
export const PHONE_CODE_EXPIRED = "PHONE_CODE_EXPIRED"
export const PHONE_CODE_ATTEMPTS_EXCEEDED = "PHONE_CODE_ATTEMPTS_EXCEEDED"
export const INVALID_PASSWORD = "INVALID_PASSWORD"

/** Body for the confirm-to-delete flow. `confirm` must equal `DELETE_ACCOUNT_PHRASE`. */
export interface DeleteAccountRequest {
  password: string
  confirm: string
}

/** The exact word the user must type before the account is deleted. */
export const DELETE_ACCOUNT_PHRASE = "DELETE"

/**
 * Machine-readable code the API returns on a 403 when sign-in is refused only
 * because the address is unverified. The credentials were correct, so the SPA
 * must offer a recovery path instead of discarding them.
 */
export const EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"

/**
 * One entry of the API's 422 validation array.
 *
 * The message the API sends is addressed to a person — the server renders it
 * from the field name and the constraint it defined (`validation_errors.py`),
 * so a client can show `msg` verbatim. `loc` is kept because it is the only
 * thing that ties a message to the input it belongs to, which is what lets a
 * form show the error under the right field instead of in a generic toast.
 */
export interface ValidationErrorItem {
  /** Location path, e.g. `["body", "username"]`. The last string is the field. */
  loc: (string | number)[]
  /** Pydantic's machine-readable error kind, e.g. `string_too_short`. */
  type?: string
  /** Ready-to-display message. */
  msg: string
}

export interface VerifyEmailRequest {
  token: string
}

export interface VerifyEmailResponse {
  ok: boolean
  /** True when the account was already verified before this attempt. */
  already_verified: boolean
  username: string | null
  message: string
}

export interface ResendVerificationResponse {
  message: string
}

/**
 * Body for asking the API to email a password-reset link.
 *
 * The password fields on this request are snake_case on the wire, like every
 * other auth body in this file — see `ChangePasswordRequest`.
 */
export interface ForgotPasswordRequest {
  email: string
}

/**
 * The confirmation for a reset request.
 *
 * It is byte-identical whether or not the address exists (the endpoint always
 * answers 202), so the UI must show the same thing either way and must not
treat the text as proof that a mail was sent.
 */
export interface ForgotPasswordResponse {
  message: string
}

export interface ResetPasswordRequest {
  token: string
  new_password: string
}

export interface ResetPasswordResponse {
  ok: boolean
  /**
   * The account the password was changed for, when the API echoes it.
   *
   * `null` is normal (an older API, or a response that omits it) and only means
   * the sign-in form cannot be pre-filled — never that the reset failed.
   */
  username: string | null
  message: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  refresh_token: string | null
  expires_in: number | null
}

export interface RefreshTokenRequest {
  refresh_token: string
}

// ---- Dashboard ----
export interface DashboardResponse {
  conversion_stats: ConversionStats
  storage_stats: StorageStats
  credit_balance: number
  /**
   * UTC instant the monthly credit allowance is replaced by the next period's,
   * ISO-8601. `null` when the tier has no persistent credits (nothing resets).
   * Optional because the API and the SPA deploy independently, so a new bundle
   * can briefly talk to an older API that does not send it yet.
   */
  credits_reset_at?: string | null
  tier: string
  recent_jobs_count: number
  active_api_keys: number
}

export interface ConversionStats {
  total_jobs: number
  successful_jobs: number
  failed_jobs: number
  total_credits_used: number
}

/** One bucket of the user's storage usage, keyed by file extension. */
export interface StorageBreakdownEntry {
  /** Lowercase, no leading dot. `""` means the file has no extension. */
  extension: string
  bytes: number
  file_count: number
}

export interface StorageStats {
  used_bytes: number
  limit_bytes: number
  used_percent: number
  file_count: number
  /**
   * Per-extension usage, sorted by `bytes` DESC with only `bytes > 0` entries.
   * Additive/optional: older backends omit it entirely, so a missing field must
   * always be treated as "no breakdown data" rather than an error.
   */
  breakdown?: StorageBreakdownEntry[]
  /**
   * Bytes still free in the account's quota, as the server computes it.
   *
   * Additive: an older API omits it, so it is `null` and the client pre-check
   * is skipped (the server's 413 remains authoritative either way). When it is
   * present it is the same number the Dashboard's storage card is derived
   * from, which is why the pre-check uses it rather than recomputing
   * `limit_bytes - used_bytes` in the browser.
   */
  available_bytes?: number | null
  /**
   * The largest single file the server will accept, in bytes.
   *
   * Additive for the same reason as `available_bytes`. `null` means the API is
   * older than this field, and the caller must fall back rather than assume a
   * limit.
   */
  max_file_size_bytes?: number | null
}

// ---- Upload ----
export interface CreateUploadSessionRequest {
  file_extension: string
  file_name?: string
  folder_id?: string | null
  /**
   * Total size in bytes, so the server can decide single-PUT vs multipart.
   *
   * Optional on the wire: the convert and guest flows predate it and keep
   * working without it (the server then uses its single-object path, as
   * before). The Files library upload always sends it.
   */
  file_size?: number
}

/**
 * How the server expects a session's bytes to be transferred.
 *
 * `"single"` is one PUT of the whole object to `upload_url`; `"multipart"`
 * splits it into `part_count` ranges of `part_size_bytes`, each PUT to a URL
 * minted from `/parts`. Read it through `normalizeUploadResponse`, which
 * defaults an absent or unrecognised value to `"single"` — the proven path an
 * older API supports.
 */
export type UploadMode = "single" | "multipart"

export interface UploadResponse {
  upload_id: string
  object_key: string
  /**
   * Presigned PUT URL for the whole object, or `null` for a multipart session.
   *
   * `null` is not an error: a multipart session is completed through
   * `/parts` + `/verify`, and only the latter carries a URL. Callers on the
   * single-PUT path (the convert flows) must therefore treat `null` as "this
   * session is not for me".
   */
  upload_url: string | null
  expires_in_minutes: number
  /** Defaults to `"single"` when the API is older than this field. */
  upload_mode: UploadMode
  /** Chunk size in bytes for a multipart session, else `null`. */
  part_size_bytes: number | null
  /** Number of chunks for a multipart session, else `null`. */
  part_count: number | null
  /**
   * The server's per-file size limit, echoed on every session response.
   *
   * `null` when the API is older than this field. It exists so the client can
   * refuse an over-size file before spending bandwidth on it; the server's 413
   * is still the authority.
   */
  max_file_size_bytes: number | null
}

/** One presigned part URL from `POST /uploads/sessions/{id}/parts`. */
export interface UploadPart {
  part_number: number
  url: string
}

export interface UploadSessionPartsResponse {
  parts: UploadPart[]
  expires_in_minutes: number
}

/** A completed part's ETag, as read from the object store's PUT response. */
export interface UploadPartCompletion {
  part_number: number
  /** The store's `ETag` header, quotes and all. */
  etag: string
}

/**
 * Body of `POST /uploads/sessions/{id}/verify`.
 *
 * Optional: a single-PUT session sends none, a multipart session must list
 * every part's ETag so the server can assemble the object. The same 413
 * structured errors as session creation can come back here.
 */
export interface VerifyUploadRequest {
  parts?: UploadPartCompletion[]
}

export interface UploadSession {
  upload_id: string
  object_key: string
  status: string
  file_name: string | null
  folder_id: string | null
}

/**
 * Machine-readable codes the upload endpoints return in a structured 413
 * `detail` object. The client pre-check produces the same wording, but the
 * server's message is the one rendered whenever it answers.
 */
export const FILE_TOO_LARGE = "FILE_TOO_LARGE"
export const STORAGE_QUOTA_EXCEEDED = "STORAGE_QUOTA_EXCEEDED"

// ---- Conversions ----
export interface CreateConversionJobRequest {
  source_format: string
  target_format: string
  input_key: string
  /** Raw 32-byte client-side FENCR data key, base64. Sent only when encrypting. */
  data_key?: string
  /** True when the uploaded object is a client-encrypted FENCR blob. */
  client_encrypted?: boolean
}

/** Convert a file that already lives in the user's library (object storage). */
export interface CreateLibraryConversionRequest {
  file_id: string
  target_format: string
}

export interface ConversionJobResponse {
  job_id: string
  status: string
  source_format: string
  target_format: string
  input_file: string
  output_file: string | null
  object_key: string | null
  download_url: string | null
  error_message?: string | null
  credits_used?: number
  compute_duration_ms?: number
  /**
   * Plaintext bytes moved, measured by the worker. `0` means "not measured"
   * (the job has not run, or the row predates these fields) and the UI omits
   * the line rather than rendering a zero-byte file.
   */
  input_size_bytes?: number
  output_size_bytes?: number
  /**
   * When the job row was created, ISO-8601.
   *
   * Optional because the API and the SPA deploy independently: a new bundle can
   * briefly talk to an older API that does not send it. Compose it with the
   * client-side `createdAt` through `jobCreatedAt()` rather than reading either
   * one directly.
   */
  created_at?: string | null
  /** Echoed FENCR metadata: wrapped (Fernet) data key + client-encrypted flag. */
  data_key_wrapped?: string | null
  client_encrypted?: boolean
}

/** Paginated list of a user's conversion history. */
export interface ConversionHistoryResponse {
  jobs: ConversionJobResponse[]
  total: number
  page: number
  page_size: number
}

/**
 * The windows a bulk history delete may target.
 *
 * A closed set, and the query parameter is REQUIRED by the API, so a request
 * that forgets it is rejected rather than silently interpreted as "everything".
 * `all` is only reachable by asking for it explicitly.
 */
export const HISTORY_DELETE_RANGES = ["24h", "7d", "30d", "all"] as const
export type HistoryDeleteRange = (typeof HISTORY_DELETE_RANGES)[number]

/** What a bulk delete would remove, for the confirmation dialog. */
export interface DeleteHistoryPreviewResponse {
  range: HistoryDeleteRange
  /** ISO timestamp of the lower bound actually used; null for `all`. */
  since: string | null
  count: number
  /**
   * Jobs in the window that are still running and will therefore be KEPT.
   *
   * Shown in the confirmation so the number the user agrees to and the number
   * that actually disappears cannot disagree.
   */
  active_count: number
}

/** Outcome of a bulk history delete. */
export interface DeleteHistoryRangeResponse {
  deleted_count: number
  /** Jobs inside the window that were left alone because they are still running. */
  skipped_active: number
  range: HistoryDeleteRange
}

export interface SupportedConversion {
  source_format: string
  target_format: string
}

export interface ConversionMapResponse {
  conversions: Record<string, string[]>
}

// ---- Guest (no-account) ----
/** Response from creating a guest conversion job (includes the guest token). */
export interface GuestJobResponse extends ConversionJobResponse {
  guest_token: string
}

/** A locally-stored guest conversion for the no-account history list. */
export interface GuestHistoryItem {
  job_id: string
  guest_token: string
  fileName: string
  source_format: string
  target_format: string
  status: string
  progress?: number
  errorMessage?: string
  createdAt: string
  output_file?: string | null
  download_url?: string | null
  /** Original input key/path (used to derive the download filename). */
  input_file?: string | null
  object_key?: string | null
}

// ---- Files ----
export interface FileMetadataResponse {
  id: string
  file_name: string
  file_key: string
  file_size_bytes: number
  mime_type: string
  folder_id: string | null
  is_favorite?: boolean
  created_at: string
  expires_at: string | null
}

export interface FileListResponse {
  files: FileMetadataResponse[]
  total: number
  page: number
  page_size: number
}

/** Toggle whether a file is favorited. */
export interface FavoriteFileRequest {
  is_favorite: boolean
}

/** Bulk-delete a set of files and/or folders. */
export interface BatchDeleteRequest {
  file_ids: string[]
  folder_ids: string[]
}

export interface BatchDeleteResponse {
  deleted_files: number
  deleted_folders: number
}

export interface FileDownloadResponse {
  download_url: string
  expires_in_seconds: number
}

export interface PresignedUrlsRequest {
  object_keys: string[]
  expiry_seconds?: number
}

export interface PresignedUrlResponse {
  url: string
  object_key: string
  expires_in_seconds: number
}

// ---- Folders ----
export interface FolderResponse {
  id: string
  name: string
  parent_id: string | null
  created_at: string
  updated_at: string
}

export interface FolderListResponse {
  folders: FolderResponse[]
  total: number
  page: number
  page_size: number
}

export interface FolderContentsResponse {
  folder: FolderResponse
  folders: FolderResponse[]
  files: FileMetadataResponse[]
  total_folders: number
  total_files: number
}

// ---- Credits ----
export interface CreditBalanceResponse {
  balance: number
  tier: string
  monthly_allowance: number | null
  monthly_remaining: number | null
  /** See `DashboardResponse.credits_reset_at`. */
  credits_reset_at?: string | null
}

export interface CreditTransactionResponse {
  id: string
  amount: number
  transaction_type: string
  reference_id: string | null
  description: string | null
  created_at: string
}

export interface CreditPurchaseRequest {
  amount: number
}

export interface CreditPricingResponse {
  credits: number
  price_usd: number
  price_per_credit: number
}

// ---- Subscription ----
export interface SubscriptionPlanResponse {
  tier: string
  name: string
  price_monthly_usd: number | null
  storage_gb: number
  monthly_credits: number | null
  features: string[]
}

export interface SubscriptionStatusResponse {
  tier: string
  status: string
  current_period_start: string | null
  current_period_end: string | null
  stripe_subscription_id: string | null
}

export interface CheckoutResponse {
  checkout_url: string
}

/** Stripe Customer Portal session for self-service subscription management. */
export interface PortalResponse {
  portal_url: string
}

export interface CancelSubscriptionResponse {
  message: string
  tier_after_cancel: string
}

// ---- API Keys ----
export interface APIKeyCreateRequest {
  name: string
  expires_in_days?: number
  rate_limit_per_minute?: number
}

export interface APIKeyCreateResponse {
  id: string
  name: string
  key: string
  prefix: string
  created_at: string
  expires_at: string | null
  message: string
}

export interface APIKeyListItem {
  id: string
  name: string
  prefix: string
  status: string
  created_at: string
  last_used_at: string | null
  expires_at: string | null
}

export interface APIKeyListResponse {
  keys: APIKeyListItem[]
}

// ---- SSE events ----
export interface JobProgressEvent {
  job_id: string
  status: string
  progress?: number
  message?: string
  compute_duration_ms?: number
  credits_used?: number
  input_size_bytes?: number
  output_size_bytes?: number
}

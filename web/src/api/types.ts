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
  /**
   * The library file id created by `POST /uploads/sessions/{id}/verify`.
   *
   * Additive: an older API omits it, so it stays absent rather than becoming
   * `""` — a caller that needs the id must treat `undefined` as "this API does
   * not report it" rather than uploading to an empty file.
   */
  file_id?: string
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

/**
 * What a plan's Transform AI includes.
 *
 * The whole object is absent or `null` on a plan that has no assistant (the
 * free tier), so a caller must treat `plan.ai == null` as "no AI group to
 * render" rather than as zeros. Read it through `normalizeAiEntitlement`, which
 * returns `null` for a payload it cannot trust instead of a half-built object.
 */
export interface AiEntitlement {
  /** `"standard" | "advanced" | "priority"`, as the API names the model tier. */
  model_level: string
  /** Ready-to-display tier name, e.g. `"Advanced"`. */
  model_label: string
  requests_per_hour: number
  /** Files one message may carry on this plan. */
  max_attachments: number
  /** Largest single document the assistant will read, in megabytes. */
  max_document_mb: number
  /** Tool calls the model may make in one turn. */
  max_actions_per_turn: number
}

export interface SubscriptionPlanResponse {
  tier: string
  name: string
  price_monthly_usd: number | null
  storage_gb: number
  monthly_credits: number | null
  features: string[]
  /**
   * The assistant this plan includes, or `null`/absent when it has none.
   *
   * Additive and per the same independent-deploy rule as every optional field
   * here: an older API omits it entirely, and "the API didn't say" must render
   * as no AI group — never as a plan with zero of everything.
   */
  ai?: AiEntitlement | null
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
  // The terminal (COMPLETED) event carries the stored object key, because a
  // converter may emit a container rather than the target format
  // (`pdf -> jpg` on a multi-page PDF emits a `.zip`).
  output_file?: string | null
}

// ---- Transform AI (assistant) ----
//
// Every field below is optional unless the UI literally cannot function without
// it, for the same independent-deploy reason as the profile fields above: the
// SPA and the API ship separately, so a new bundle can briefly talk to an older
// API. Always read these through the `normalizeAssistant*` helpers in
// `api/normalize.ts` rather than indexing into a raw response.

/**
 * Which model backend answered.
 *
 * `"unknown"` is not an API value — it is what `normalizeAssistantStatus`
 * substitutes for a value the contract does not define (or omits), so the UI can
 * tell "a real model" apart from "echo/demo mode" without inventing an answer.
 */
export type AssistantBackend = "openai" | "echo" | "unknown"

export interface AssistantStatus {
  enabled: boolean
  backend: AssistantBackend
  model: string

  // ---- Plan entitlements (additive; every field is optional) ----
  //
  // An older API omits all of these, so each is optional and the UI degrades to
  // the pre-entitlement behaviour: no usage label, the client's own attachment
  // fallback. `max_attachments` in particular must never be read as `0` when it
  // is absent — see `normalizeAssistantStatus`.

  /** `"FREE" | "PRO" | "PRO_PLUS" | "ENTERPRISE"`, echoing the caller's plan. */
  tier?: string
  /** `"standard" | "advanced" | "priority"`. */
  model_level?: string
  /** Ready-to-display tier name, e.g. `"Advanced"`. */
  model_label?: string
  requests_per_hour?: number
  used_this_hour?: number
  remaining_this_hour?: number
  /** How many files one turn may attach on this plan. */
  max_attachments?: number
  max_actions_per_turn?: number
  /** Largest single document the assistant will read, in bytes. */
  max_document_bytes?: number
}

export interface AssistantConversation {
  id: string
  title: string
  created_at: string
  /** Absent on the create response; present on list/detail. */
  updated_at?: string
}

export interface AssistantConversationListResponse {
  conversations: AssistantConversation[]
}

export type AssistantMessageRole = "user" | "assistant" | "tool"

/**
 * A library file the user attached to a chat turn.
 *
 * It is a *reference* (`user_files.id`), not a copy: the server resolves the id
 * to the account's own file and answers `ATTACHMENT_NOT_FOUND` when it is not
 * owned or no longer exists. `extension` is optional because the server may not
 * echo it, and the UI derives it from `name` when it is absent.
 */
export interface AssistantAttachment {
  id: string
  name: string
  extension?: string
}

export interface AssistantMessage {
  id: string
  role: AssistantMessageRole
  content: string
  tool_name?: string | null
  meta?: Record<string, unknown> | null
  created_at?: string
  /**
   * Files the user attached to a `user` turn, parsed from `meta.attachments`.
   * Present on a persisted user message so a reopened conversation still shows
   * its attachment chips; absent on rows that carry none.
   */
  attachments?: AssistantAttachment[]
  /**
   * Human label for a step, parsed from `meta.label`. Present on a `tool` row
   * of a conversation persisted after the label contract landed; absent on
   * legacy rows, where the client falls back to a phrase built from
   * `tool_name` (see `lib/assistantTranscript.ts#toolStepLabel`).
   */
  label?: string
  /** Tool-result summary, parsed from `meta.summary`. Absent on legacy rows. */
  summary?: string
  /**
   * Files and jobs the row refers to, parsed from `meta.artifacts` — on a
   * `tool` row and on the final assistant message of a turn. Absent when the
   * row has none, or on an API older than the field.
   */
  artifacts?: AssistantArtifact[]
}

export interface AssistantConversationDetailResponse {
  conversation: AssistantConversation
  messages: AssistantMessage[]
}

export interface AssistantChatRequest {
  message: string
  conversation_id?: string
  /** Library file ids to attach to this turn. The API caps this per plan. */
  file_ids?: string[]
  /**
   * The page the question was asked from, e.g. `"/app/dashboard"`.
   *
   * Lets the model answer "what's on this page?" style questions. Sent as the
   * pathname only — never a full URL, which would leak the query string.
   */
  context?: string
}

/**
 * A file, folder, job or pending deletion the assistant surfaced while
 * answering.
 *
 * A `"folder"` artifact carries the folder's own `id` in `id`, its name in
 * `name`, and its location in `meta.parent_id` (null at the drive root) — the
 * opposite direction to a file artifact's `meta.folder_id`. `"delete"` carries
 * a *proposal* to delete a library file, with its lifecycle in
 * `meta.state` (`lib/assistantDeletion.ts` maps that to the card the user sees)
 * and its conversation in `meta.conversation_id`, precisely so the card does not
 * need the conversation threaded down through the transcript — which also
 * renders inside the floating mini chat, where no conversation id exists.
 * `"unknown"` stays the fallback for a kind this bundle does not recognise (an
 * API newer than the SPA), so such an artifact still renders as a neutral chip
 * instead of being dropped.
 */
export interface AssistantArtifact {
  type: "file" | "job" | "folder" | "delete" | "unknown"
  id: string
  name: string
  meta?: Record<string, unknown> | null
}

/**
 * The lifecycle state of a `delete` artifact, read from its `meta.state`.
 *
 * `"pending"` is the only one that is actionable; the others are records of a
 * decision already made, which is what a reloaded conversation shows.
 */
export type AssistantDeletionState = "pending" | "deleted" | "cancelled" | "failed"

/** Body of `POST /assistant/conversations/{id}/deletions`. */
export interface AssistantDeletionRequest {
  file_id: string
  approve: boolean
}

/** The server's answer: the outcome, which the card shows instead of buttons. */
export interface AssistantDeletionResponse {
  file_id: string
  file_name: string
  state: Exclude<AssistantDeletionState, "pending">
}

export interface AssistantToolEvent {
  name: string
  label?: string
  status: "running" | "done" | "unknown"
  summary?: string
  artifacts?: AssistantArtifact[]
}

/**
 * A fully parsed frame of the `POST /assistant/chat` SSE stream.
 *
 * A discriminated union on `type` (the SSE `event:` name), so `applyStreamEvent`
 * can switch exhaustively and a frame the parser does not recognise is dropped
 * before it reaches the reducer rather than silently mutating state.
 */
export type AssistantStreamEvent =
  | { type: "status"; stage: string }
  | { type: "delta"; text: string }
  | { type: "tool"; tool: AssistantToolEvent }
  | { type: "artifact"; artifact: AssistantArtifact }
  | {
      type: "done"
      conversation_id: string
      message_id: string
      content: string
      artifacts: AssistantArtifact[]
      /**
       * The persisted id of the USER message this turn answered, when the API
       * sends one.
       *
       * Optional for the same independent-deploy reason as every other
       * additive field here: an older API omits it entirely, and an absent id
       * must stay absent rather than becoming `""`.
       *
       * Its purpose is editing. A user message sent *in this session* only has
       * a local `user-N` id until the turn finishes; this frame is what gives
       * it a real server id without a reload, which the edit-and-resend flow
       * needs in order to truncate the persisted transcript. See
       * `lib/assistantChat.ts#editTurn` and `lib/useAssistantChat.ts#editMessage`.
       */
      user_message_id?: string
    }
  | { type: "error"; code: string; message: string }

export interface AssistantSummaryResponse {
  file_id: string
  file_name: string
  summary: string
  key_points: string[]
  model: string
}

export interface AssistantRecommendation {
  target_format: string
  label: string
  category: string
  reason: string
  /** 0..1 model confidence; clamped by the normaliser. */
  confidence: number
}

export interface AssistantRecommendRequest {
  file_id?: string
  source_format?: string
  use_case?: string
}

export interface AssistantRecommendResponse {
  source_format: string
  use_case: string
  recommendations: AssistantRecommendation[]
}

/** Machine-readable assistant error codes the UI branches on. */
export const ASSISTANT_QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
export const ASSISTANT_NOT_AVAILABLE_FOR_TIER = "AI_NOT_AVAILABLE_FOR_TIER"
export const ASSISTANT_INTERNAL_ERROR = "INTERNAL_ERROR"
/**
 * Returned (403) when a turn names more files than the plan allows. Unlike
 * `ATTACHMENT_NOT_FOUND`, nothing is wrong with the files themselves: the user
 * simply attached too many, so the composer keeps the whole list and lets them
 * drop one and resend.
 */
export const ASSISTANT_ATTACHMENT_LIMIT_EXCEEDED = "ATTACHMENT_LIMIT_EXCEEDED"
/**
 * Returned (404) when a turn's `file_ids` names a library file the account does
 * not own or that no longer exists. The composer drops the offending
 * attachments and keeps the typed message so the user can resend without it.
 */
export const ASSISTANT_ATTACHMENT_NOT_FOUND = "ATTACHMENT_NOT_FOUND"
/**
 * Returned (404) when an approve/reject names a file with no pending deletion
 * proposal in that conversation — usually because another tab already resolved
 * it. The card reports the failure and stops offering the buttons rather than
 * retrying against a proposal that no longer exists.
 */
export const ASSISTANT_DELETION_NOT_FOUND = "DELETION_NOT_FOUND"

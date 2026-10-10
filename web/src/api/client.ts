import { downloadFromUrl, isTrustedDownloadUrl, saveBlob } from '@/lib/download'
import { createRequestCache } from '@/lib/requestCache'
import { encryptFileToFencr, fencrDataKeyToBase64 } from '@/lib/fencr'
import { joinValidationMessages, validationErrorsFrom } from '@/lib/apiErrors'
import { readSseStream } from '@/lib/sse'
import {
  normalizeApiKeyCreate,
  normalizeAssistantConversation,
  normalizeAssistantConversationDetail,
  normalizeAssistantConversationList,
  normalizeAssistantDeletion,
  normalizeAssistantRecommend,
  normalizeAssistantStatus,
  normalizeAssistantStreamEvent,
  normalizeAssistantSummary,
  normalizeBatchConversion,
  normalizeBatchStatus,
  normalizeWorkflow,
  normalizeWorkflowList,
  normalizeWorkflowRun,
  normalizeApiKeyList,
  normalizeBatchDelete,
  normalizeConnectedApp,
  normalizeConnectedAppList,
  normalizeMcpConsent,
  normalizeMcpConsentApproval,
  normalizeMcpFolderAccess,
  normalizeCancelSubscription,
  normalizeChangePlan,
  normalizeCheckout,
  normalizeConversionHistory,
  normalizeConversionJob,
  normalizeConversionMap,
  normalizeCreditBalance,
  normalizeCreditHistory,
  normalizeCreditPreference,
  normalizeCreditPricing,
  normalizeDashboard,
  normalizeDeleteHistoryPreview,
  normalizeDeleteHistoryRange,
  normalizeFile,
  normalizeFileDownload,
  normalizeFileList,
  normalizeFolder,
  normalizeFolderContents,
  normalizeFolderList,
  normalizeForgotPassword,
  normalizeGuestJob,
  normalizeInvoices,
  normalizeJobProgressEvent,
  normalizePhoneStatus,
  normalizePaymentMethodList,
  normalizePaymentMethodSession,
  normalizePortal,
  normalizePresignedUrls,
  normalizeResendVerification,
  normalizeResetPassword,
  normalizeResumeSubscription,
  normalizeSubscriptionPlans,
  normalizeSubscriptionStatus,
  normalizeSupportedConversions,
  normalizeTokenResponse,
  normalizeUploadResponse,
  normalizeUploadSession,
  normalizeUploadSessionParts,
  normalizeUser,
  normalizeVerifyEmail,
} from './normalize'
import {
  normalizeApiLogDetail,
  normalizeApiLogList,
  normalizeApiMetrics,
  normalizeMcpActivityList,
  normalizeMcpConnections,
  normalizeMcpControl,
  normalizeMcpSummary,
} from './developerNormalize'
import type {
  ApiLogFilters,
  ApiLogRange,
  ApiMetricsResponse,
  McpActivityFilters,
} from './developerTypes'
import type {
  APIKeyCreateRequest,
  AssistantChatRequest,
  AssistantDeletionRequest,
  AssistantRecommendRequest,
  AssistantStreamEvent,
  BatchDeleteRequest,
  ChangePasswordRequest,
  ChangePlanRequest,
  ConversionJobResponse,
  CreateWorkflowRequest,
  CreditPurchaseRequest,
  CreateConversionJobRequest,
  CreateLibraryConversionRequest,
  CreateUploadSessionRequest,
  DeleteAccountRequest,
  FavoriteFileRequest,
  ForgotPasswordRequest,
  GuestJobResponse,
  HistoryDeleteRange,
  McpConsentApprovalRequest,
  PresignedUrlsRequest,
  RefreshTokenRequest,
  RequestPhoneVerificationRequest,
  ResetPasswordRequest,
  SubscriptionCheckoutRequest,
  TokenResponse,
  UpdateProfileRequest,
  UpdateConnectedAppRequest,
  UserCreateRequest,
  ValidationErrorItem,
  VerifyPhoneRequest,
  VerifyUploadRequest,
} from './types'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '')
const TOKEN_KEY = 'transform_access_token'
const REFRESH_KEY = 'transform_refresh_token'

/**
 * The API's origin, i.e. `API_BASE` without its `/api` suffix.
 *
 * The API serves a few paths at its *root* rather than under `/api` — its
 * OpenAPI docs (`/docs`, `/redoc`, `/openapi.json`) and its health probes
 * (`/health`, `/ready`). Those must be built from this origin: the SPA is
 * hosted separately, so a same-origin `/docs` would load the SPA itself and
 * render the router's empty catch-all page.
 *
 * Empty when `API_BASE` is a same-origin prefix (`/api` in local dev, where
 * the Vite dev server proxies both `/api` and the root API paths).
 */
export const API_ORIGIN = API_BASE.replace(/\/api$/, '')

/** True for an absolute `http(s)` URL (vs. a server-relative path). */
const ABSOLUTE_URL = /^https?:\/\//i

/**
 * FENCR client-side encryption is streamed chunk-by-chunk with WebCrypto, so it
 * can handle large files in principle, but the backend contract targets inputs
 * under 1 GB. Files at or above this limit skip encryption and go plaintext.
 */
const CLIENT_ENCRYPTION_LIMIT_BYTES = 1024 * 1024 * 1024 // 1 GB

/** The exact detail the backend returns when `ENCRYPTION_MASTER_KEY` is unset. */
const CLIENT_ENCRYPTION_DISABLED_MSG = 'Client-side encryption is not enabled on this deployment'

/**
 * True when an error is the backend rejecting `client_encrypted: true` because
 * the master key isn't configured. We sniff the message (the backend returns it
 * as the 400 detail) so the caller can gracefully re-run the flow plaintext.
 */
function isClientEncryptionDisabledError(err: unknown): boolean {
  return err instanceof Error && err.message.includes(CLIENT_ENCRYPTION_DISABLED_MSG)
}

/**
 * Resolve a server-relative URL returned in an API payload (e.g. a streaming
 * `download_url`) to an absolute fetch path.
 *
 * The backend returns these paths with a leading `/api/…`. `API_BASE` also
 * carries the `/api` prefix, so naively concatenating the two produces a
 * doubled prefix (`/api/api/…`). This helper joins them correctly, and is
 * safe whether `API_BASE` is a local prefix (`/api`) or an absolute origin
 * (`https://api.example.com/api`).
 *
 * Exported so pages that fetch a server-returned path directly (rather than
 * through `ApiClient.request`) resolve it against the configured API origin
 * instead of the SPA origin — which matters now that the SPA is hosted
 * separately (cross-origin) from the API.
 */
export function resolveServerPath(path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  // Strip any leading `/api`/`/api/` from the returned path. The backend emits
  // download URLs already prefixed with `/api/…`, and `API_BASE` also carries
  // the `/api` prefix, so naively concatenating yields a doubled `/api/api/…`.
  // We keep a single copy and never double-prefix.
  const withoutApi = path.replace(/^\/api(?=\/|$)/, '')
  return `${API_BASE}${withoutApi.startsWith('/') ? withoutApi : `/${withoutApi}`}`
}

/**
 * An error thrown by {@link ApiClient}, carrying the HTTP status and — when the
 * API sends one — a machine-readable code.
 *
 * The code exists because a status alone is not enough to choose a recovery
 * path: a 403 from `POST /users/token` can mean several things, and only the
 * `EMAIL_NOT_VERIFIED` case should show "check your inbox / resend" rather than
 * a generic failure. `instanceof Error` still holds, so every existing
 * `err instanceof Error` call site keeps working unchanged.
 */
export class ApiError extends Error {
  readonly status: number
  readonly code?: string
  /**
   * The full structured `detail` object, for fields beyond `code`/`message`.
   *
   * The unverified-sign-in response carries the account's own address
   * alongside the code so the sign-in page can offer a working "resend" button
   * without asking the user to retype it. That is not a disclosure: the branch
   * is only reachable with a correct username *and* password.
   */
  readonly details?: Record<string, unknown>
  /**
   * Per-field messages from a 422, when the API sent the validation array.
   *
   * Exposed separately from `details` (which is the *object* shape of
   * `detail`) because a form needs to place each message under the input it
   * names, and a joined sentence cannot be taken apart again. `message` carries
   * the same text joined into one line, so a caller with no form to fill in
   * still only has to read `err.message`.
   */
  readonly validationErrors?: ValidationErrorItem[]

  constructor(
    message: string,
    status: number,
    code?: string,
    details?: Record<string, unknown>,
    validationErrors?: ValidationErrorItem[],
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
    this.validationErrors = validationErrors
  }
}

/**
 * Extract a human-readable message (and optional code) from an error response.
 *
 * FastAPI serialises an `HTTPException`'s `detail` verbatim, and this API uses
 * more than one shape:
 *
 *   - a plain string for most errors,
 *   - `{ code, message }` where the client must branch
 *     (e.g. `EMAIL_NOT_VERIFIED` on an unverified sign-in),
 *   - an array of `{ loc, type, msg }` objects for 422 request-validation
 *     failures. `msg` is written for a person by the API's own
 *     `RequestValidationError` handler — it names the field and the rule, so it
 *     is shown as-is. `loc` rides along because it is what lets a form put each
 *     message under the right input.
 *
 * Reading only the string shape turned the structured case into the literal
 * text `"[object Object]"`, which is how this was found.
 */
async function readErrorBody(
  response: Response,
  fallback: string,
): Promise<{
  detail: string
  code?: string
  details?: Record<string, unknown>
  validationErrors?: ValidationErrorItem[]
}> {
  let body: unknown
  try {
    body = await response.json()
  } catch {
    // A non-JSON error body (a proxy's HTML 502, an empty 500) must not throw a
    // parse error on top of the real failure.
    return { detail: response.statusText || fallback }
  }

  const detail = (body as { detail?: unknown } | null)?.detail

  if (typeof detail === 'string' && detail) return { detail }

  if (Array.isArray(detail)) {
    const validationErrors = validationErrorsFrom(detail)
    const messages = validationErrors.map((entry) => entry.msg)
    if (messages.length) {
      // `joinValidationMessages`, not `join(', ')`: several messages are
      // sentences, and a comma between two full stops reads as a typo
      // ("Username is required., Password must be…").
      return { detail: joinValidationMessages(messages), validationErrors }
    }
  }

  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const structured = detail as Record<string, unknown>
    const code = typeof structured.code === 'string' ? structured.code : undefined
    const message = typeof structured.message === 'string' ? structured.message : undefined
    if (message) return { detail: message, code, details: structured }
    if (code) return { detail: code, code, details: structured }
  }

  return { detail: response.statusText || fallback }
}

/** A request, with an optional cache window for GETs. */
interface RequestOptions extends RequestInit {
  /**
   * How long a GET response may be reused, in milliseconds. Omit to leave that
   * call uncached. `0` coalesces concurrent identical requests but retains
   * nothing.
   */
  cacheTtlMs?: number
}

/**
 * The typed HTTP client.
 *
 * Exported (alongside the `api` singleton) so a module that receives the client
 * as a parameter — rather than reaching for the singleton — can name its type
 * and depend on just the methods it uses (`Pick<ApiClient, …>`).
 */
export class ApiClient {
  /**
   * Short-lived cache for repeatable GETs.
   *
   * WHY: every SPA navigation used to refetch the same payloads. Measured on the
   * running app, a single Dashboard load issued six `/user/dashboard` requests
   * (three independent callers) and four return visits to the Dashboard issued
   * four identical ones.
   *
   * ACCURACY: an entry lives only for its TTL, and **any successful non-GET
   * clears the whole cache** (see `request`), so a mutation can never be
   * followed by a stale read. The cache is also dropped on a token change, so
   * one account can never read another's data.
   */
  private readonly cache = createRequestCache()

  /**
   * Forget every cached read.
   *
   * Called by the job stream when a conversion finishes (the balance and the
   * history both changed) so the listeners that refresh on `credits:updated`
   * cannot be served the pre-completion value they were just told to replace.
   */
  invalidateCache(): void {
    this.cache.invalidateAll()
  }

  private get token() {
    return localStorage.getItem(TOKEN_KEY)
  }

  private get refreshToken() {
    return localStorage.getItem(REFRESH_KEY)
  }

  setTokens(auth: TokenResponse) {
    localStorage.setItem(TOKEN_KEY, auth.access_token)
    if (auth.refresh_token) localStorage.setItem(REFRESH_KEY, auth.refresh_token)
    // The identity may have changed, so anything cached belongs to someone else.
    this.cache.invalidateAll()
  }

  clearTokens() {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_KEY)
    this.cache.invalidateAll()
  }

  isAuthenticated() {
    return Boolean(this.token)
  }

  private async refresh() {
    const rt = this.refreshToken
    if (!rt) return false
    try {
      const res = await fetch(`${API_BASE}/users/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rt } satisfies RefreshTokenRequest),
      })
      if (!res.ok) return false
      const data = normalizeTokenResponse(await res.json())
      this.setTokens(data)
      return true
    } catch {
      return false
    }
  }

  /**
   * A request with an optional cache window.
   *
   * `cacheTtlMs` opts a **GET** into the read cache: concurrent callers for the
   * same path share one round trip, and the response is reused for the TTL.
   * Omit it and the request behaves exactly as before (no dedupe, no reuse),
   * which keeps the change opt-in and reviewable per endpoint.
   *
   * `0` means "coalesce only": callers on one page load share a single request
   * but nothing is retained. That is the setting for identity-critical reads
   * such as `/users/me`, where holding a value would be a correctness risk
   * rather than a staleness one.
   */
  private async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const { cacheTtlMs, ...init } = options
    const method = (init.method ?? 'GET').toUpperCase()

    if (method === 'GET' && cacheTtlMs !== undefined) {
      return this.cache.run(path, cacheTtlMs, () => this.send<T>(path, init))
    }

    const result = await this.send<T>(path, init)
    if (method !== 'GET') {
      // Any successful write changes server state, so nothing we remembered
      // about it can still be trusted. Clearing the whole cache rather than
      // tracking which reads a mutation affects is the choice that cannot be
      // wrong: the cost is a refetch, which is what happened before anyway.
      this.cache.invalidateAll()
    }
    return result
  }

  /**
   * Perform one HTTP request: auth header, one silent refresh on 401, and error
   * mapping. Kept separate from `request` so the cache wrapper measures only
   * the network work, and so a cached read is indistinguishable from a live one
   * to every caller.
   */
  private async send<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = {
      ...((options.headers as Record<string, string>) ?? {}),
    }
    if (this.token) headers.Authorization = `Bearer ${this.token}`
    // JSON payloads sent by this client need the correct Content-Type or
    // FastAPI will reject the body with HTTP 422. A `FormData` body is the one
    // exception: the browser must set `multipart/form-data` itself, because
    // only it knows the boundary it generated. Overriding that here broke every
    // multipart upload with a 422 that named no field.
    const isFormData =
      typeof FormData !== 'undefined' && options.body instanceof FormData
    if (options.body != null && !isFormData && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json'
    }

    let res = await fetch(`${API_BASE}${path}`, { ...options, headers })

    // Attempt one silent refresh on 401
    if (res.status === 401 && !path.startsWith('/users/token') && this.refreshToken) {
      const refreshed = await this.refresh()
      if (refreshed) {
        headers.Authorization = `Bearer ${this.token}`
        res = await fetch(`${API_BASE}${path}`, { ...options, headers })
      }
    }

    if (res.status === 401) {
      this.clearTokens()
      window.dispatchEvent(new CustomEvent('auth:unauthorized'))
    }
    if (!res.ok) {
      const { detail, code, details, validationErrors } = await readErrorBody(res, res.statusText)
      throw new ApiError(detail, res.status, code, details, validationErrors)
    }

    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  }

  // ---- Auth ----
  register = (body: UserCreateRequest) =>
    this.request<unknown>('/users/register', { method: 'POST', body: JSON.stringify(body) }).then(
      normalizeUser,
    )

  /**
   * Consume an email-verification token.
   *
   * A 400 is expected and meaningful here (the link was already used, or it
   * expired), so the caller must surface the message rather than treat every
   * failure as a network fault.
   */
  verifyEmail = (token: string) =>
    this.request<unknown>('/users/verify-email', {
      method: 'POST',
      body: JSON.stringify({ token }),
    }).then(normalizeVerifyEmail)

  /**
   * Ask for a fresh verification link. Answers 202 regardless of whether the
   * address exists, so the caller must show the same confirmation either way.
   */
  resendVerification = (email: string) =>
    this.request<unknown>('/users/resend-verification', {
      method: 'POST',
      body: JSON.stringify({ email }),
    }).then(normalizeResendVerification)

  /**
   * Ask for a password-reset link.
   *
   * Answers 202 unconditionally, with the same body whether or not the address
   * exists — anything else would turn the endpoint into an account-existence
   * oracle. The caller must therefore show the same confirmation either way and
   * must not tell the user that a mail was in fact sent.
   */
  forgotPassword = (email: string) =>
    this.request<unknown>('/users/forgot-password', {
      method: 'POST',
      body: JSON.stringify({ email } satisfies ForgotPasswordRequest),
    }).then(normalizeForgotPassword)

  /**
   * Consume a reset token and set a new password.
   *
   * A 400 is expected and meaningful here (the link was already used, or it
   * expired), so the caller must surface the message rather than treat every
   * failure as a network fault. Other devices keep their existing access tokens
   * until they expire (30 minutes) — this deployment has no server-side token
   * store, so a reset cannot sign anyone else out.
   */
  resetPassword = (body: ResetPasswordRequest) =>
    this.request<unknown>('/users/reset-password', {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(normalizeResetPassword)

  login = async (username: string, password: string) => {
    const form = new URLSearchParams({ username, password })
    const res = await fetch(`${API_BASE}/users/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
    })
    if (!res.ok) {
      // Shares the response reader with `request` so a structured detail
      // (EMAIL_NOT_VERIFIED) survives here too — this path does not throw a
      // plain Error string, and the sign-in page branches on the code.
      const { detail, code, details, validationErrors } = await readErrorBody(res, 'Invalid credentials')
      throw new ApiError(detail, res.status, code, details, validationErrors)
    }
    const data = normalizeTokenResponse(await res.json())
    // Persist tokens immediately so subsequent calls are authenticated.
    this.setTokens(data)
    return data
  }

  // `0`: coalesce the concurrent `/users/me` calls a single page load makes,
  // but never retain the result. This is the identity every guard reads, so a
  // remembered value is a correctness risk rather than a staleness one.
  me = () => this.request<unknown>('/users/me', { cacheTtlMs: 0 }).then(normalizeUser)

  /**
   * Update the editable profile fields.
   *
   * Names only — `username` is the login identifier (it appears in the token
   * subject) and `email` is gated behind re-verification, so neither is
   * writable here. An empty string clears a name; the server normalises that to
   * `null` so "cleared" and "never set" are one state.
   */
  updateProfile = (body: UpdateProfileRequest) =>
    this.request<unknown>('/users/me', { method: 'PATCH', body: JSON.stringify(body) }).then(
      normalizeUser,
    )

  /**
   * Change the password, proving knowledge of the current one.
   *
   * Answers 204. Other devices keep their existing access tokens until they
   * expire (30 minutes) — this deployment has no server-side token store, so a
   * claim to have revoked them would be false.
   */
  changePassword = (body: ChangePasswordRequest) =>
    this.request<void>('/users/me/password', { method: 'POST', body: JSON.stringify(body) })

  /**
   * Replace the profile picture.
   *
   * Multipart rather than the presigned-URL flow used for conversions: an
   * avatar is capped at 2 MB and is downscaled server-side before storage, so
   * the extra round trip and the bucket CORS surface buy nothing here.
   */
  uploadAvatar = (file: File) => {
    const form = new FormData()
    form.append('file', file, file.name || 'avatar')
    return this.request<unknown>('/users/me/avatar', { method: 'POST', body: form }).then(
      normalizeUser,
    )
  }

  /** Remove the profile picture, reverting the account to its initials tile. */
  deleteAvatar = () =>
    this.request<unknown>('/users/me/avatar', { method: 'DELETE' }).then(normalizeUser)

  // ---- Phone verification ----
  /**
   * Submit a number and send it a code. Answers 202; nothing is verified until
   * the code comes back through `verifyPhone`.
   */
  requestPhoneVerification = (body: RequestPhoneVerificationRequest) =>
    this.request<unknown>('/users/me/phone', { method: 'POST', body: JSON.stringify(body) }).then(
      normalizePhoneStatus,
    )

  /** Send a fresh code to the number already on file. Answers 202. */
  resendPhoneVerification = () =>
    this.request<unknown>('/users/me/phone/resend', { method: 'POST' }).then(normalizePhoneStatus)

  /**
   * Consume a code.
   *
   * A 400 is meaningful here (wrong, expired, or already-used code) and a 429
   * means the attempt limit was reached, so callers must branch on the code
   * rather than treating every failure as a network fault.
   */
  verifyPhone = (body: VerifyPhoneRequest) =>
    this.request<unknown>('/users/me/phone/verify', {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(normalizePhoneStatus)

  /** Forget the number and its verification. */
  removePhone = () => this.request<void>('/users/me/phone', { method: 'DELETE' })

  /**
   * Permanently delete the account and everything it owns.
   *
   * Requires both the password and a typed confirmation phrase, so a stray
   * click on a live session cannot destroy an account. Answers 204; the caller
   * must clear the tokens and leave the app, since the session it holds is now
   * meaningless.
   */
  deleteAccount = (body: DeleteAccountRequest) =>
    this.request<void>('/users/me', { method: 'DELETE', body: JSON.stringify(body) })

  // ---- Dashboard ----
  /**
   * Storage, credits and conversion stats.
   *
   * Cached briefly because three independent callers want it on one page load
   * (`DashboardPage`'s mount effect, its `credits:updated` listener, and
   * `UploadsContext.refreshLimits`) and every return visit to the Dashboard
   * fetched it again. 15s is short enough that a balance is never meaningfully
   * stale, and a mutation or a finished conversion clears it outright.
   */
  dashboard = () =>
    this.request<unknown>('/v1/user/dashboard', { cacheTtlMs: 15_000 }).then(normalizeDashboard)
  profile = () => this.request<unknown>('/v1/user/profile').then(normalizeUser)

  // ---- Upload (presigned URL flow) ----
  createUploadSession = (body: CreateUploadSessionRequest) =>
    this.request<unknown>('/uploads/sessions', { method: 'POST', body: JSON.stringify(body) }).then(
      normalizeUploadResponse,
    )

  getUploadSession = (id: string) =>
    this.request<unknown>(`/uploads/sessions/${id}`).then(normalizeUploadSession)

  /**
   * Mint presigned URLs for a batch of a multipart session's parts.
   *
   * Batching is the caller's job (see the upload engine): asking for every part
   * of a 5 GB upload up front would make the whole transfer depend on URLs that
   * expire while later parts are still queued, and the server only has to sign
   * the requested numbers. A 409 means the session is not multipart.
   */
  createUploadSessionParts = (id: string, partNumbers: number[]) =>
    this.request<unknown>(`/uploads/sessions/${id}/parts`, {
      method: 'POST',
      body: JSON.stringify({ part_numbers: partNumbers }),
    }).then(normalizeUploadSessionParts)

  /**
   * Finalise an upload session.
   *
   * `body.parts` is required only for a multipart session — it carries each
   * part's ETag so the server can assemble the object — and omitted for the
   * single-PUT path, which is what the convert and guest flows use (`jobId` is
   * their optional query parameter and is unaffected).
   */
  verifyUpload = (id: string, jobId?: string, body?: VerifyUploadRequest) =>
    this.request<unknown>(
      `/uploads/sessions/${id}/verify${jobId ? `?job_id=${encodeURIComponent(jobId)}` : ''}`,
      {
        method: 'POST',
        ...(body ? { body: JSON.stringify(body) } : {}),
      },
    ).then(normalizeUploadSession)

  cancelUpload = (id: string) =>
    this.request<void>(`/uploads/sessions/${id}`, { method: 'DELETE' })

  /**
   * Upload file bytes directly to the presigned URL (no auth header needed).
   *
   * IMPORTANT: the backend signs the presigned PUT URL WITHOUT a Content-Type
   * header. Sending one (e.g. `application/octet-stream`) causes B2/S3 to
   * reject the request with 403 SignatureDoesNotMatch, and it forces an extra
   * CORS preflight. So we deliberately omit Content-Type and let the browser
   * send the body as-is.
   *
   * Cross-origin PUT to the object store still requires the bucket's CORS
   * policy to allow the app origin + `PUT`. If this throws "Failed to fetch",
   * the bucket CORS rule is missing/incorrect.
   */
  async putToPresignedUrl(url: string, blob: Blob): Promise<void> {
    let res: Response;
    try {
      res = await fetch(url, {
        method: "PUT",
        body: blob,
        mode: "cors",
      });
    } catch (err) {
      const message =
        err instanceof Error && err.message === "Failed to fetch"
          ? "The object store blocked the upload (CORS). Configure the bucket CORS policy to allow this origin and the PUT method."
          : err instanceof Error
            ? err.message
            : "Upload failed";
      throw new Error(message);
    }
    if (!res.ok) {
      // Distinguish the two common failure modes for a better error message.
      if (res.status === 403) {
        throw new Error(
          "Upload was rejected by the object store (403). This is usually a presigned-URL signature mismatch or a bucket CORS/permission issue.",
        );
      }
      throw new Error(`Upload failed (${res.status})`);
    }
  }

  // ---- Conversions ----
  supportedConversions = () =>
    this.request<unknown>('/conversions/supported').then(normalizeSupportedConversions)
  /** Map of source format -> valid target formats. */
  conversionMap = () =>
    this.request<unknown>('/conversions/supported/map').then(normalizeConversionMap)
  createConversion = (body: CreateConversionJobRequest) =>
    this.request<unknown>('/conversions/jobs', { method: 'POST', body: JSON.stringify(body) }).then(
      normalizeConversionJob,
    )
  /**
   * Convert a file that already exists in the user's library (object storage).
   * The server infers the source format from the stored file's extension and
   * enqueues the job immediately.
   */
  convertLibraryFile = (fileId: string, targetFormat: string) =>
    this.request<unknown>('/conversions/jobs', {
      method: 'POST',
      body: JSON.stringify({ file_id: fileId, target_format: targetFormat } satisfies CreateLibraryConversionRequest),
    }).then(normalizeConversionJob)
  getJob = (id: string) =>
    this.request<unknown>(`/conversions/jobs/${id}`).then(normalizeConversionJob)
  /**
   * Paginated conversion history for the current user, optionally time-ranged.
   *
   * Cached briefly because the Jobs provider AND every page's mount effect ask
   * for it, so navigating between Queue, History and Convert re-fetched an
   * identical list each time. Live progress does not depend on this: the job
   * rows come from the store and are updated by the SSE stream, and a finished
   * conversion clears the cache outright.
   */
  conversionHistory = (page = 1, pageSize = 20, range?: string) =>
    this.request<unknown>(
      `/conversions/history?page=${page}&page_size=${pageSize}${range ? `&range=${encodeURIComponent(range)}` : ''}`,
      { cacheTtlMs: 10_000 },
    ).then(normalizeConversionHistory)
  /** Delete a single history record owned by the current user. */
  deleteHistoryJob = (id: string) =>
    this.request<void>(`/conversions/history/${id}`, { method: 'DELETE' })

  /**
   * Count what a bulk history delete would remove, for the confirmation dialog.
   *
   * Read-only, so it is safe to call while merely opening the menu.
   */
  deleteHistoryPreview = (range: HistoryDeleteRange) =>
    this.request<unknown>(
      `/conversions/history/delete-preview?range=${encodeURIComponent(range)}`,
    ).then(normalizeDeleteHistoryPreview)

  /**
   * Delete every finished job in a time window.
   *
   * `range` is required — the endpoint rejects a request without one so that a
   * missing parameter can never mean "delete everything". Jobs still running
   * are skipped and reported back, never removed out from under a worker.
   */
  deleteHistoryRange = (range: HistoryDeleteRange) =>
    this.request<unknown>(`/conversions/history?range=${encodeURIComponent(range)}`, {
      method: 'DELETE',
    }).then(normalizeDeleteHistoryRange)
  /** Retry a failed job without re-uploading its input file. */
  retryJob = (id: string) =>
    this.request<unknown>(`/conversions/jobs/${id}/retry`, { method: 'POST' }).then(normalizeConversionJob)

  /**
   * Convert several library files into one target format in a single request.
   *
   * Returns a per-item outcome rather than all-or-nothing: an item that could
   * not start carries its own reason, and the rest still begin. The response's
   * `batch_id` is what the caller uses to re-read the batch later (see
   * `batchStatus`), so a reload or a closed tab does not lose it.
   *
   * Deliberately NOT cached: this is a write, and the shared request cache
   * invalidates itself on every write anyway, so caching it would only add a
   * way for a stale body to be replayed.
   */
  batchConvert = (fileIds: string[], targetFormat: string) =>
    this.request<unknown>('/conversions/batch', {
      method: 'POST',
      body: JSON.stringify({ file_ids: fileIds, target_format: targetFormat }),
    }).then(normalizeBatchConversion)

  /**
   * Re-read a batch and its items from the jobs that carry its id.
   *
   * This is what lets a batch survive a page reload: the client asks for the
   * batch instead of having to have kept the job ids it was handed at creation.
   * An unknown or unowned id comes back as an empty batch, not an error.
   */
  batchStatus = (batchId: string) =>
    this.request<unknown>(`/conversions/batches/${encodeURIComponent(batchId)}`).then(
      normalizeBatchStatus,
    )

  // ---- Saved workflows ----

  listWorkflows = () =>
    this.request<unknown>('/v1/workflows').then(normalizeWorkflowList)

  createWorkflow = (body: CreateWorkflowRequest) =>
    this.request<unknown>('/v1/workflows', {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(normalizeWorkflow)

  updateWorkflow = (id: string, body: CreateWorkflowRequest) =>
    this.request<unknown>(`/v1/workflows/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }).then(normalizeWorkflow)

  deleteWorkflow = (id: string) =>
    this.request<void>(`/v1/workflows/${encodeURIComponent(id)}`, { method: 'DELETE' })

  /**
   * Run a saved workflow against files chosen now.
   *
   * The files are named per run and never stored in the workflow, so a saved
   * workflow cannot outlive the access the user had when they saved it: the
   * server re-resolves and re-authorizes every file on each run.
   */
  runWorkflow = (id: string, fileIds: string[]) =>
    this.request<unknown>(`/v1/workflows/${encodeURIComponent(id)}/runs`, {
      method: 'POST',
      body: JSON.stringify({ file_ids: fileIds }),
    }).then(normalizeWorkflowRun)

  /**
   * Run the full "normal conversion" flow with a file: create the job, open an
   * upload session, PUT the bytes to the presigned URL, then verify/enqueue.
   *
   * Client-side encryption (FENCR): for files under the 1 GB limit, the file is
   * encrypted in the browser into a FENCR blob BEFORE upload, and the per-file
   * data key (base64) + `client_encrypted: true` are passed to the backend with
   * the job. If the deployment has no `ENCRYPTION_MASTER_KEY` set, the backend
   * rejects the encrypted request with "Client-side encryption is not enabled";
   * we then transparently retry WITHOUT encryption using the original file, so
   * the feature degrades gracefully. Files at/over 1 GB keep the plaintext path.
   *
   * Used for the retry fallback when a job's input object is gone from storage.
   */
  async convertWithFile(
    source: string,
    target: string,
    file: Blob,
    fileName?: string,
  ): Promise<ConversionJobResponse> {
    const name = fileName || (file instanceof File ? file.name : 'file')
    // Client-side encryption requires the file to fit under the FENCR streaming
    // limit. Larger files go straight down the plaintext path.
    const canEncrypt = file.size < CLIENT_ENCRYPTION_LIMIT_BYTES

    // Encrypt first (before creating the job) so a Master-Key-unset failure can
    // be detected before we ever create an upload session or PUT the blob.
    const fencr = canEncrypt ? await encryptFileToFencr(file) : null

    const makeJob = (encrypted: boolean) =>
      this.createConversion({
        source_format: source,
        target_format: target,
        input_key: name,
        ...(encrypted && fencr
          ? { data_key: fencrDataKeyToBase64(fencr.dataKey), client_encrypted: true }
          : {}),
      })

    let job: ConversionJobResponse
    try {
      job = await makeJob(Boolean(fencr))
    } catch (err) {
      // The backend only rejects `client_encrypted: true` when the master key is
      // unset. Fall back to plaintext in that case so the feature degrades
      // gracefully instead of erroring the whole conversion.
      if (fencr && isClientEncryptionDisabledError(err)) {
        job = await makeJob(false)
      } else {
        throw err
      }
    }

    // The object we PUT: the FENCR ciphertext when encrypted, else the original.
    const uploadBytes = fencr ? fencr.encrypted : file

    // 2. Create an upload session.
    const upload = await this.createUploadSession({ file_extension: source, file_name: name })
    // 3. Upload bytes directly to the presigned URL (no Content-Type header —
    //    the URL is signed without one, so sending it would 403 on B2/S3).
    await this.putToPresignedUrl(this.singlePutUrl(upload), uploadBytes)
    // 4. Verify upload completion and enqueue the job.
    await this.verifyUpload(upload.upload_id, job.job_id)
    return job
  }

  /**
   * The whole-object URL a single-PUT flow needs, or a clear failure.
   *
   * A session the server answered with `upload_mode: "multipart"` carries no
   * whole-object URL (the parts flow owns it). Nothing on this path requests
   * multipart — `convertWithFile` is capped well below the split threshold and
   * sends no `file_size` — but returning `""` to `putToPresignedUrl` would turn
   * "unsupported session" into a confusing request against the SPA's own
   * origin, so it is named here instead.
   */
  private singlePutUrl(upload: { upload_url: string | null }): string {
    if (!upload.upload_url) {
      throw new Error(
        'This file is too large for a single upload. Upload it from the Files page instead.',
      )
    }
    return upload.upload_url
  }

  /**
   * Check whether an object still exists in object storage. Returns true if it
   * does, false if it's missing (used to decide retry vs. re-upload fallback).
   */
  async objectExists(objectKey: string | null): Promise<boolean> {
    if (!objectKey) return false
    try {
      await this.getPresignedUrls({ object_keys: [objectKey] })
      return true
    } catch {
      return false
    }
  }

  /** Relative path to the (auth-required) streaming download endpoint. */
  getJobDownloadUrl = (id: string) => `/conversions/jobs/${id}/download`

  /** Relative path to the (auth-required) streaming endpoint for a library file. */
  getFileStreamUrl = (id: string) => `/v1/files/${id}/stream`

  /**
   * Fetch a download URL with the Authorization header, retrying once after a
   * silent token refresh on 401 — the same recovery `request()` applies to JSON
   * calls, so a streamed download never fails just because the access token
   * expired while the page was open.
   */
  private async authedFetch(url: string): Promise<Response> {
    const init = (): RequestInit => ({
      headers: this.token ? { Authorization: `Bearer ${this.token}` } : {},
    })
    let res = await fetch(url, init())
    if (res.status === 401 && this.refreshToken && (await this.refresh())) {
      res = await fetch(url, init())
    }
    return res
  }

  /**
   * Run an authenticated request and hand back the raw `Response`.
   *
   * The sibling of `authedFetch` for a request that is not a bare GET and whose
   * body must stay unread by `request()` (which always consumes it as JSON) —
   * the assistant chat needs to stream the body itself. Mirrors `request`'s
   * 401 handling so a streamed call recovers from an expired access token the
   * same way every other call does.
   */
  private async authorizedFetch(path: string, options: RequestInit = {}): Promise<Response> {
    const init = (): RequestInit => ({
      ...options,
      headers: {
        ...((options.headers as Record<string, string>) ?? {}),
        ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
      },
    })

    let res = await fetch(`${API_BASE}${path}`, init())
    if (res.status === 401 && this.refreshToken && (await this.refresh())) {
      res = await fetch(`${API_BASE}${path}`, init())
    }
    if (res.status === 401) {
      this.clearTokens()
      window.dispatchEvent(new CustomEvent('auth:unauthorized'))
    }
    return res
  }

  /**
   * Stream a server-relative path to a browser download.
   *
   * `auth: false` is used by the guest flow, which authenticates with its token
   * in the query string instead of a bearer header (and so must not turn the
   * request into a credentialed one).
   */
  private async saveStreamedDownload(
    path: string,
    filename: string,
    { query = '', auth = true }: { query?: string; auth?: boolean } = {},
  ): Promise<void> {
    const url = `${resolveServerPath(path)}${query}`
    const res = auth ? await this.authedFetch(url) : await fetch(url)
    if (!res.ok) throw new Error(`Download failed (${res.status})`)
    saveBlob(await res.blob(), filename)
  }

  /**
   * Save a server-supplied download URL, preferring the pre-signed absolute URL
   * the API returned.
   *
   * An absolute URL is only handed to the browser when its scheme is trusted
   * (`https:`, or loopback `http:` for local dev). A rejected scheme — e.g. a
   * plain-http URL from a misconfigured deployment — falls back to the
   * authenticated streaming endpoint (`fallbackPath`) rather than navigating the
   * tab to an untrusted host or failing silently.
   */
  private async saveDownload(
    url: string,
    fallbackPath: string,
    filename: string,
    stream: { query?: string; auth?: boolean } = {},
  ): Promise<void> {
    if (!ABSOLUTE_URL.test(url)) {
      await this.saveStreamedDownload(url, filename, stream)
      return
    }
    if (!downloadFromUrl(url, filename)) {
      await this.saveStreamedDownload(fallbackPath, filename, stream)
    }
  }

  /**
   * Download the converted output of a completed job.
   *
   * Fetches the job's download URL from the download endpoint (a pre-signed GET
   * URL when encryption is off, or the authed streaming endpoint when on), then
   * triggers a browser download of the converted file.
   */
  async downloadConvertedFile(jobId: string, filename?: string): Promise<void> {
    const job = await this.getJob(jobId)
    if (job.status !== 'COMPLETED' || !job.download_url) {
      throw new Error('This conversion is not ready to download yet.')
    }
    await this.saveDownload(
      job.download_url,
      this.getJobDownloadUrl(jobId),
      filename ?? jobOutputFilename(job),
    )
  }

  // ---- Guest (no-account) ----
  /** Map of source format -> valid target formats for guest conversions. */
  guestConversionMap = () =>
    this.request<unknown>('/guest/conversions/supported/map').then(normalizeConversionMap)

  /** Create a guest conversion job. Returns the job + its guest token. */
  guestCreateJob = (body: CreateConversionJobRequest) =>
    this.request<unknown>('/guest/conversions/jobs', {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(normalizeGuestJob)

  /** Open a guest upload session (returns the presigned PUT URL). */
  guestCreateUploadSession = (body: CreateUploadSessionRequest) =>
    this.request<unknown>('/guest/uploads/sessions', {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(normalizeUploadResponse)

  /**
   * Upload guest file bytes directly to the presigned URL (no Content-Type
   * header — the URL is signed without one, so sending it would 403).
   */
  guestPutToPresignedUrl = (url: string, blob: Blob) => this.putToPresignedUrl(url, blob)

  /** Verify a guest upload and enqueue its conversion job. */
  guestVerifyUpload = (uploadId: string, jobId: string, guestToken: string) =>
    this.request<unknown>(
      `/guest/uploads/sessions/${uploadId}/verify?job_id=${encodeURIComponent(jobId)}&guest_token=${encodeURIComponent(guestToken)}`,
      { method: 'POST' },
    ).then(normalizeUploadSession)

  /**
   * Run the full guest conversion flow with a file: create the job, open an
   * upload session, PUT the bytes to the presigned URL, then verify/enqueue.
   *
   * Mirrors `convertWithFile`'s client-side FENCR encryption + graceful
   * fallback: files under 1 GB are encrypted in the browser before upload and
   * the data key (base64) + `client_encrypted: true` are sent with the job. If
   * the deployment has no master key the backend returns "Client-side encryption
   * is not enabled", and we retry once plaintext. Returns the created job.
   */
  async guestConvertWithFile(
    source: string,
    target: string,
    file: Blob,
    fileName?: string,
  ): Promise<GuestJobResponse> {
    const name = fileName || (file instanceof File ? file.name : 'file')
    const canEncrypt = file.size < CLIENT_ENCRYPTION_LIMIT_BYTES
    const fencr = canEncrypt ? await encryptFileToFencr(file) : null

    const makeJob = (encrypted: boolean) =>
      this.guestCreateJob({
        source_format: source,
        target_format: target,
        input_key: name,
        ...(encrypted && fencr
          ? { data_key: fencrDataKeyToBase64(fencr.dataKey), client_encrypted: true }
          : {}),
      })

    let job: GuestJobResponse
    try {
      job = await makeJob(Boolean(fencr))
    } catch (err) {
      if (fencr && isClientEncryptionDisabledError(err)) {
        job = await makeJob(false)
      } else {
        throw err
      }
    }

    const guestToken = job.guest_token
    const uploadBytes = fencr ? fencr.encrypted : file

    const upload = await this.guestCreateUploadSession({
      file_extension: source,
      file_name: name,
    })
    await this.guestPutToPresignedUrl(this.singlePutUrl(upload), uploadBytes)
    await this.guestVerifyUpload(upload.upload_id, job.job_id, guestToken)
    return job
  }

  /** Fetch a single guest job by id + token. */
  guestGetJob = (jobId: string, guestToken: string) =>
    this.request<unknown>(
      `/guest/conversions/jobs/${jobId}?guest_token=${encodeURIComponent(guestToken)}`,
    ).then(normalizeGuestJob)

  /**
   * Subscribe to SSE progress for a guest job. No Authorization header — the
   * guest token travels as a query parameter. Returns a cleanup function.
   */
  guestSubscribeToJob(
    id: string,
    guestToken: string,
    handlers: {
      onProgress: (evt: import('./types').JobProgressEvent) => void
      onError: (msg: string) => void
      onDone: () => void
      onConnected?: () => void
    },
  ): () => void {
    return subscribeToJobStream(
      `${API_BASE}/guest/events/jobs/${id}?guest_token=${encodeURIComponent(guestToken)}`,
      handlers,
    )
  }

  /**
   * Download a completed guest conversion's output.
   *
   * Mirrors the authed `downloadConvertedFile` download semantics:
   *   - A pre-signed absolute URL → navigate directly (no CORS / no token).
   *   - A relative URL → stream via the API with the guest token appended.
   *
   * Self-healing: the worker emits the COMPLETED SSE event *before* persisting
   * the output file, so the UI can show "Ready" before the job has a
   * downloadable output yet — leaving a stale/absent `download_url` behind. So
   * when the URL is missing we re-fetch, and retry briefly over the persist
   * race before giving up.
   */
  async guestDownload(job: {
    job_id?: string | null
    download_url?: string | null
    input_file?: string | null
    /** Object key the worker produced; its extension drives the download name. */
    output_file?: string | null
    target_format: string
    guest_token: string
  }): Promise<void> {
    let url = job.download_url

    // Re-fetch (with a short retry) when the stored URL is missing, to ride out
    // the COMPLETED-before-persist race in the worker.
    if (!url && job.job_id) {
      for (let attempt = 0; attempt < 3 && !url; attempt++) {
        if (attempt > 0) await new Promise((r) => setTimeout(r, 350))
        const fresh = await this.guestGetJob(job.job_id, job.guest_token)
        url = fresh.download_url
      }
    }
    if (!url) {
      throw new Error('This conversion is not ready to download yet.')
    }

    const guestQuery = `?guest_token=${encodeURIComponent(job.guest_token)}`
    const guestStreamPath = `/guest/conversions/jobs/${job.job_id}/download`
    if (ABSOLUTE_URL.test(url) && !job.job_id) {
      // Nothing to fall back to if the scheme is rejected.
      if (!downloadFromUrl(url, jobOutputFilename(job))) {
        throw new Error('Download failed: the download URL was rejected.')
      }
      return
    }
    await this.saveDownload(url, guestStreamPath, jobOutputFilename(job), {
      query: guestQuery,
      auth: false,
    })
  }

  /**
   * Download a file from the library (auth-required unless the API returned a
   * pre-signed URL). Shares the streaming/auth/blob path with the conversion
   * download so both resolve the API origin and recover from a 401 identically.
   */
  async downloadLibraryFile(fileId: string, filename: string): Promise<void> {
    const { download_url } = await this.getFileDownload(fileId)
    await this.saveDownload(download_url, this.getFileStreamUrl(fileId), filename)
  }

  /**
   * Fetch a library file's bytes as a Blob, for an in-page preview.
   *
   * Resolves the bytes the same way `downloadLibraryFile` does, because the API
   * answers with one of two things: a pre-signed absolute URL (encryption off)
   * or a server-relative path to the authenticated streaming endpoint
   * (encryption on).
   *
   * WHY the same allowlist/fallback as the download: an API-supplied URL is
   * handed to `fetch()`, and the browser would follow it wherever it points —
   * `javascript:`, `data:` or a plain-http host included. An untrusted scheme
   * must never reach the browser, so it falls back to the streaming endpoint
   * instead. That fallback is not merely safer, it is the only correct path in
   * the encrypted case: the streaming endpoint is what DECRYPTS an at-rest
   * encrypted object, so the pre-signed object would come back as ciphertext.
   *
   * `authedFetch` repeats the request once after the silent 401 refresh, so
   * previewing a file whose access token expired while the page was open works.
   */
  async fetchLibraryFileBlob(fileId: string): Promise<Blob> {
    const { download_url } = await this.getFileDownload(fileId)

    // A trusted absolute URL is a pre-signed GET: it carries its own signature,
    // so it needs no Authorization header (sending one can invalidate the
    // signature on some providers).
    if (isTrustedDownloadUrl(download_url)) {
      const res = await fetch(download_url)
      if (!res.ok) throw new Error(`Download failed (${res.status})`)
      return res.blob()
    }

    // Relative path, or a scheme we refuse to hand to the browser: stream it
    // through the API, which authenticates and decrypts.
    const res = await this.authedFetch(resolveServerPath(this.getFileStreamUrl(fileId)))
    if (!res.ok) throw new Error(`Download failed (${res.status})`)
    return res.blob()
  }

  /**
   * Fetch a completed conversion's output bytes, together with the name those
   * bytes must be stored under.
   *
   * A job is NOT a library file: its ids live in a different space, so
   * `fetchLibraryFileBlob(jobId)` answers `404 File not found` (and the two id
   * spaces are both UUIDs, so that failure reads as a deleted file rather than
   * as the wrong endpoint). A job's output is only reachable through the job.
   *
   * The URL is re-read from the API rather than taken from the row: `GET
   * /conversions/jobs/{id}` is what decides where the bytes are. With at-rest
   * encryption on it answers a server-relative path to the streaming endpoint,
   * which returns the DECRYPTED object; without encryption it answers a
   * pre-signed absolute URL to the plaintext object. Reading the stored object
   * directly would put ciphertext (the server's `TRENC` container) into a
   * preview or a saved file.
   *
   * Same allowlist, same fallback and the same silent 401 refresh as
   * `fetchLibraryFileBlob` — one auth path, not a second one.
   *
   * The filename comes from *here*, alongside the bytes, because the name a
   * file is stored under must match its content: a converter may emit a
   * container rather than the target format (a multi-page `pdf -> jpg` job
   * produces a `.zip`), so the authoritative job record decides the name — a
   * client-side row may not have received the terminal SSE event yet and would
   * name those `.zip` bytes `.jpg`.
   */
  async fetchConversionOutput(jobId: string): Promise<{ blob: Blob; filename: string }> {
    const job = await this.getJob(jobId)
    const url = job.download_url ?? this.getJobDownloadUrl(jobId)
    const filename = jobOutputFilename(job)

    if (isTrustedDownloadUrl(url)) {
      const res = await fetch(url)
      if (!res.ok) throw new Error(`Could not read that file (${res.status})`)
      return { blob: await res.blob(), filename }
    }

    // A relative streaming path is used as-is; an absolute URL we refuse to
    // hand to the browser falls back to the job's streaming endpoint.
    const path = ABSOLUTE_URL.test(url) ? this.getJobDownloadUrl(jobId) : url
    const res = await this.authedFetch(resolveServerPath(path))
    if (!res.ok) throw new Error(`Could not read that file (${res.status})`)
    return { blob: await res.blob(), filename }
  }

  /**
   * The output bytes of a completed conversion.
   *
   * Thin wrapper over `fetchConversionOutput`, which owns the URL allowlist,
   * the fallback and the silent 401 refresh; callers that also need the
   * authoritative filename should call that method directly.
   */
  async fetchConversionOutputBlob(jobId: string): Promise<Blob> {
    const { blob } = await this.fetchConversionOutput(jobId)
    return blob
  }

  // ---- Files ----
  listFolders = () =>
    this.request<unknown>('/v1/files/folders', { cacheTtlMs: 10_000 }).then(normalizeFolderList)
  /** Get the subfolders + files directly inside a folder. */
  getFolderContents = (folderId: string) =>
    this.request<unknown>(`/v1/files/folders/${folderId}`, { cacheTtlMs: 10_000 }).then(
      normalizeFolderContents,
    )
  createFolder = (name: string, parentId?: string | null) =>
    this.request<unknown>('/v1/files/folders', {
      method: 'POST',
      body: JSON.stringify({ name, parent_id: parentId ?? null }),
    }).then(normalizeFolder)
  renameFolder = (id: string, name: string) =>
    this.request<unknown>(`/v1/files/folders/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }).then(normalizeFolder)
  deleteFolder = (id: string) => this.request<void>(`/v1/files/folders/${id}`, { method: 'DELETE' })
  listFiles = (folderId?: string | null) =>
    this.request<unknown>(
      `/v1/files${folderId ? `?folder_id=${encodeURIComponent(folderId)}` : ''}`,
      { cacheTtlMs: 10_000 },
    ).then(
      normalizeFileList,
    )
  getFile = (id: string) => this.request<unknown>(`/v1/files/${id}`).then(normalizeFile)
  /**
   * Search the caller's own files by name, across every folder.
   *
   * Backs the composer's `@` document picker. Deliberately NOT cached: the
   * client cache is keyed on the path, so a cached search would replay one
   * query's results for a different one, and the picker issues a fresh request
   * per debounced keystroke anyway.
   *
   * The server scopes results to the caller and answers a blank query with
   * nothing, so an empty box can never list the whole drive.
   */
  searchFiles = (query: string, page = 1, pageSize = 20) =>
    this.request<unknown>(
      `/v1/files/search?q=${encodeURIComponent(query)}&page=${page}&page_size=${pageSize}`,
    ).then(normalizeFileList)
  deleteFile = (id: string) => this.request<void>(`/v1/files/${id}`, { method: 'DELETE' })
  renameFile = (id: string, name: string) =>
    this.request<unknown>(`/v1/files/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }).then(normalizeFile)
  moveFile = (id: string, folderId: string | null) =>
    this.request<unknown>(`/v1/files/${id}/move`, {
      method: 'POST',
      body: JSON.stringify({ folder_id: folderId }),
    }).then(normalizeFile)
  getFileDownload = (id: string) =>
    this.request<unknown>(`/v1/files/${id}/download`).then(normalizeFileDownload)
  getPresignedUrls = (body: PresignedUrlsRequest) =>
    this.request<unknown>('/v1/files/urls', { method: 'POST', body: JSON.stringify(body) }).then(
      normalizePresignedUrls,
    )
  /** Set whether a file is a favorite (pinned to the favorites view). */
  setFileFavorite = (id: string, isFavorite: boolean) =>
    this.request<unknown>(`/v1/files/${id}/favorite`, {
      method: 'PATCH',
      body: JSON.stringify({ is_favorite: isFavorite } satisfies FavoriteFileRequest),
    }).then(normalizeFile)
  /** Paginated list of the user's favorited files. */
  listFavorites = (page = 1, pageSize = 50) =>
    this.request<unknown>(`/v1/files/favorites?page=${page}&page_size=${pageSize}`).then(normalizeFileList)
  /** Delete multiple files and/or folders in one request. */
  batchDelete = (body: BatchDeleteRequest) =>
    this.request<unknown>('/v1/files/batch-delete', {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(normalizeBatchDelete)
  /** Move a folder under a new parent (pass null to move to the root). */
  moveFolder = (id: string, parentId: string | null) =>
    this.request<unknown>(`/v1/files/folders/${id}/move`, {
      method: 'POST',
      body: JSON.stringify({ parent_id: parentId }),
    }).then(normalizeFolder)

  // ---- Credits ----
  creditBalance = () =>
    this.request<unknown>('/v1/credits/balance', { cacheTtlMs: 30_000 }).then(normalizeCreditBalance)
  /**
   * Persist the credit spend-order preference.
   *
   * Only the preference is sent: the wallet balances are never client-writable,
   * so a request cannot mint credits. Applies to API-origin conversions only.
   */
  setCreditPreference = (purchasedCreditsFirst: boolean) =>
    this.request<unknown>('/v1/credits/preference', {
      method: 'PATCH',
      body: JSON.stringify({ purchased_credits_first: purchasedCreditsFirst }),
    }).then(normalizeCreditPreference)
  creditHistory = () =>
    this.request<unknown>('/v1/credits/history', { cacheTtlMs: 30_000 }).then(normalizeCreditHistory)
  /**
   * Start a credit-pack purchase.
   *
   * `uiMode` is only ever *sent* as `embedded`, so a build that cannot render
   * an embedded form produces byte-identical requests to the ones this app made
   * before embedded checkout existed.
   */
  purchaseCredits = (amount: number, uiMode?: 'embedded' | 'elements') =>
    this.request<unknown>('/v1/credits/purchase', {
      method: 'POST',
      body: JSON.stringify({
        amount,
        ...(uiMode ? { ui_mode: uiMode } : {}),
      } satisfies CreditPurchaseRequest),
    }).then(normalizeCheckout)
  creditPricing = () =>
    this.request<unknown>('/v1/credits/pricing', { cacheTtlMs: 300_000 }).then(normalizeCreditPricing)
  // ---- Subscription ----
  subscriptionPlans = () =>
    this.request<unknown>('/v1/subscription/plans').then(normalizeSubscriptionPlans)
  subscriptionStatus = () =>
    this.request<unknown>('/v1/subscription/status', { cacheTtlMs: 30_000 }).then(
      normalizeSubscriptionStatus,
    )
  /** Open a Stripe Customer Portal session for self-service billing management. */
  createPortalSession = () =>
    this.request<unknown>('/v1/subscription/portal', { method: 'POST' }).then(normalizePortal)
  checkout = (tier: string, uiMode?: 'embedded' | 'elements', promotionCode?: string) =>
    this.request<unknown>('/v1/subscription/checkout', {
      method: 'POST',
      body: JSON.stringify({
        tier,
        ...(uiMode ? { ui_mode: uiMode } : {}),
        // Only sent when a code is present, so a request without one is
        // byte-identical to the one this app made before promo codes existed.
        ...(promotionCode ? { promotion_code: promotionCode } : {}),
      } satisfies SubscriptionCheckoutRequest),
    }).then(normalizeCheckout)
  cancelSubscription = () =>
    this.request<unknown>('/v1/subscription/cancel', { method: 'POST' }).then(
      normalizeCancelSubscription,
    )
  /**
   * Undo a scheduled cancellation, so the subscription renews again.
   *
   * Only meaningful while `cancel_at_period_end` is true; the API answers 400
   * for a subscription that is not scheduled to end, which the caller shows as
   * a plain failure.
   */
  resumeSubscription = () =>
    this.request<unknown>('/v1/subscription/resume', { method: 'POST' }).then(
      normalizeResumeSubscription,
    )
  /**
   * The account's invoices, for the billing-history table.
   *
   * Deliberately **uncached**: an invoice is created by Stripe on a payment
   * that happens outside this tab, so a remembered list would keep showing a
   * pre-payment state for its whole TTL with nothing to invalidate it.
   */
  listInvoices = () =>
    this.request<unknown>('/v1/subscription/invoices').then(normalizeInvoices)
  /**
   * Move an existing paid subscription to another self-serve tier.
   *
   * The response is plain JSON (no redirect), so there is nothing to guard with
   * `trustedExternalUrl`. A 409 means the account has no subscription to change
   * (a Free user must go through checkout), and a 400 covers the same tier or a
   * tier that is not self-serve — both are handled by the caller.
   */
  changePlan = (tier: string) =>
    this.request<unknown>('/v1/subscription/change-plan', {
      method: 'POST',
      body: JSON.stringify({ tier } satisfies ChangePlanRequest),
    }).then(normalizeChangePlan)
  /**
   * Create a Stripe Customer Session for the in-page Payment Element.
   *
   * `enabled: false` is a normal answer (Stripe unconfigured, or no customer
   * yet), so the caller hides the section rather than reporting an error.
   */
  paymentMethodSession = () =>
    this.request<unknown>('/v1/subscription/payment-method-session', { method: 'POST' }).then(
      normalizePaymentMethodSession,
    )
  /**
   * The account's saved cards, for the in-app card list.
   *
   * Deliberately **uncached**, unlike `subscriptionStatus`. A card can also be
   * added out of band — the Payment Element's `confirmSetup` talks to Stripe
   * directly and never passes through this client — so a remembered list would
   * keep showing the pre-add cards for its whole TTL with nothing to invalidate
   * it.
   */
  listPaymentMethods = () =>
    this.request<unknown>('/v1/subscription/payment-methods').then(normalizePaymentMethodList)
  /**
   * Make one saved card the default for future charges.
   *
   * Answers with the **new** list, so the caller updates state from the response
   * rather than issuing a second, racier read.
   */
  setDefaultPaymentMethod = (id: string) =>
    this.request<unknown>(`/v1/subscription/payment-methods/${id}/default`, {
      method: 'POST',
    }).then(normalizePaymentMethodList)
  /**
   * Detach one saved card.
   *
   * Answers 409 (and this client raises `ApiError`) when the card is the
   * customer's current default while an active subscription depends on it; the
   * server's message tells the user to choose another default first, so the
   * caller must show `err.message` rather than a generic failure.
   */
  removePaymentMethod = (id: string) =>
    this.request<unknown>(`/v1/subscription/payment-methods/${id}`, {
      method: 'DELETE',
    }).then(normalizePaymentMethodList)

  // ---- API Keys ----
  createApiKey = (body: APIKeyCreateRequest) =>
    this.request<unknown>('/v1/api-keys', { method: 'POST', body: JSON.stringify(body) }).then(
      normalizeApiKeyCreate,
    )
  listApiKeys = () =>
    this.request<unknown>('/v1/api-keys', { cacheTtlMs: 30_000 }).then(normalizeApiKeyList)
  deleteApiKey = (id: string) => this.request<void>(`/v1/api-keys/${id}`, { method: 'DELETE' })

  // ---- Connected AI applications (MCP) ----
  /**
   * Applications the user has connected to their account.
   *
   * These are not API keys: each one was authorized through a browser consent
   * flow, holds a scoped, revocable token, and can be disconnected without the
   * user touching a password or a key.
   */
  listConnectedApps = () =>
    this.request<unknown>('/v1/mcp/connected-apps', { cacheTtlMs: 15_000 }).then(
      normalizeConnectedAppList,
    )
  /**
   * Disconnect one application. Takes effect on the agent's very next request,
   * because access tokens are resolved through the (now revoked) grant rather
   * than trusted on their own.
   */
  revokeConnectedApp = (id: string) =>
    this.request<void>(`/v1/mcp/connected-apps/${id}`, { method: 'DELETE' })

  /**
   * Replace an existing connection's permissions in place.
   *
   * The body is the *full* desired set rather than a delta, so a request can
   * never be interpreted as a partial update that leaves a permission the user
   * just removed. `confirm_destructive` must be true to add `documents.delete`;
   * `buildUpdateConnectedAppRequest` sets both invariants, so this method takes
   * a body that already satisfies the contract.
   *
   * Answers with the updated row, so the caller rerenders from the response
   * rather than guessing at the new state.
   */
  updateConnectedApp = (id: string, body: UpdateConnectedAppRequest) =>
    this.request<unknown>(`/v1/mcp/connected-apps/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }).then(normalizeConnectedApp)

  /** The authorization request an application is asking the user to approve. */
  mcpConsentRequest = (params: {
    client_id: string
    redirect_uri: string
    scope: string
    resource?: string
  }) =>
    this.request<unknown>(`/v1/mcp/authorize?${new URLSearchParams(params).toString()}`).then(
      normalizeMcpConsent,
    )
  /**
   * Record the user's decision.
   *
   * `approved_scopes` is intersected server-side with the scopes the request
   * actually asked for, so this page cannot approve a permission the user was
   * never shown.
   */
  approveMcpConsent = (body: McpConsentApprovalRequest) =>
    this.request<unknown>('/v1/mcp/authorize', {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(normalizeMcpConsentApproval)

  /**
   * The folders an active agent may reach, for the Files page indicator.
   *
   * Fail-soft on the caller's side: a deployment without the endpoint answers
   * 404, and the Files page must simply render without the decoration.
   */
  mcpFolderAccess = () =>
    this.request<unknown>('/v1/mcp/folder-access', { cacheTtlMs: 15_000 }).then(
      normalizeMcpFolderAccess,
    )

  // ---- Transform AI (assistant) ----

  /** Whether the assistant is on, and which backend answers. */
  assistantStatus = () =>
    this.request<unknown>('/v1/assistant/status').then(normalizeAssistantStatus)

  assistantConversations = () =>
    this.request<unknown>('/v1/assistant/conversations').then(normalizeAssistantConversationList)

  assistantCreateConversation = (title?: string) =>
    this.request<unknown>('/v1/assistant/conversations', {
      method: 'POST',
      body: JSON.stringify(title ? { title } : {}),
    }).then(normalizeAssistantConversation)

  assistantConversation = (id: string) =>
    this.request<unknown>(`/v1/assistant/conversations/${id}`).then(
      normalizeAssistantConversationDetail,
    )

  assistantDeleteConversation = (id: string) =>
    this.request<void>(`/v1/assistant/conversations/${id}`, { method: 'DELETE' })

  /**
   * Delete one message of a conversation, and everything after it.
   *
   * This is the server half of "edit a message": the transcript the API holds is
   * the source of truth, so an edit has to drop the original turn (and the
   * answer it produced) or reopening the conversation would show the message the
   * user thought they had replaced. The caller re-sends the edited turn
   * afterwards, which appends at the freed position.
   *
   * A conversation that is not the caller's and a message that is not in it both
   * answer 404, so neither can be probed.
   */
  assistantDeleteMessage = (conversationId: string, messageId: string) =>
    this.request<void>(
      `/v1/assistant/conversations/${conversationId}/messages/${messageId}`,
      { method: 'DELETE' },
    )

  /**
   * Approve or reject the assistant's pending proposal to delete a file.
   *
   * The assistant may only *propose* a deletion; this call is the user actually
   * deciding. `approve: true` deletes the file permanently, `false` cancels the
   * proposal — the endpoint is the same either way, which is why this is one
   * method with a boolean rather than two.
   *
   * The conversation id is in the path and the file id in the body because a
   * proposal belongs to a conversation: the same file could in principle be
   * proposed twice, and only the pair identifies which one is being answered. A
   * pair with no pending proposal answers 404 `DELETION_NOT_FOUND`, which the
   * card surfaces rather than retries.
   *
   * Being a non-GET, `request` clears the read cache on success, so the file
   * listing and the drive breakdown are refetched fresh on the next navigation
   * instead of serving a cached row for a file that no longer exists.
   */
  assistantResolveDeletion = (conversationId: string, body: AssistantDeletionRequest) =>
    this.request<unknown>(`/v1/assistant/conversations/${conversationId}/deletions`, {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(normalizeAssistantDeletion)

  /** Summarise one library file. */
  assistantSummarize = (fileId: string) =>
    this.request<unknown>('/v1/assistant/summarize', {
      method: 'POST',
      body: JSON.stringify({ file_id: fileId }),
    }).then(normalizeAssistantSummary)

  /** Ask for target-format recommendations for a file and/or a source format. */
  assistantRecommend = (body: AssistantRecommendRequest) =>
    this.request<unknown>('/v1/assistant/recommend', {
      method: 'POST',
      body: JSON.stringify(body),
    }).then(normalizeAssistantRecommend)

  /**
   * Send a chat turn and read the streamed answer.
   *
   * The endpoint is a **POST** that answers with a streamed `text/event-stream`
   * body, so `EventSource` cannot be used (it only issues GETs and carries no
   * Authorization header). This reads the response through the same
   * silent-refresh path as every other authenticated call, then frames the body
   * with the shared SSE parser.
   *
   * @returns an abort function — it stops the fetch and the stream, which is
   * what the composer's Stop button calls.
   */
  assistantChat(
    body: AssistantChatRequest,
    handlers: {
      onEvent: (event: AssistantStreamEvent) => void
      onError: (error: { code: string; message: string }) => void
      onDone?: () => void
    },
  ): () => void {
    const controller = new AbortController()

    void (async () => {
      try {
        const res = await this.authorizedFetch('/v1/assistant/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
          body: JSON.stringify(body),
          signal: controller.signal,
        })

        if (!res.ok || !res.body) {
          const { detail, code } = await readErrorBody(res, res.statusText || 'Assistant unavailable')
          handlers.onError({ code: code ?? 'INTERNAL_ERROR', message: detail })
          return
        }

        await readSseStream(res, (frame) => {
          const event = normalizeAssistantStreamEvent(frame.event, frame.data)
          // An unparseable or unknown frame is dropped rather than thrown: one
          // bad frame must not end a healthy stream.
          if (event) handlers.onEvent(event)
        })
        handlers.onDone?.()
      } catch (err) {
        // An abort is the user pressing Stop, not a failure.
        if ((err as Error).name !== 'AbortError') {
          handlers.onError({ code: 'INTERNAL_ERROR', message: (err as Error).message })
        }
      }
    })()

    return () => controller.abort()
  }

  /**
   * Subscribe to SSE progress for a job. Returns a cleanup function.
   * Uses fetch-based streaming so we can send the Authorization header.
   */
  subscribeToJob(id: string, handlers: {
    onProgress: (evt: import('./types').JobProgressEvent) => void
    onError: (msg: string) => void
    onDone: () => void
    onConnected?: () => void
  }): () => void {
    const token = this.token
    return subscribeToJobStream(
      `${API_BASE}/v1/events/jobs/${id}`,
      handlers,
      token ? { Authorization: `Bearer ${token}` } : {},
    )
  }

  // ---- Developer: API Logs -----------------------------------------------

  /**
   * Aggregates for the API Logs chart and its summary cards.
   *
   * Every number in the response is computed **server-side** from the stored
   * request events. The SPA never derives a rate or a percentile: doing so would
   * let the chart and the cards disagree, and there is exactly one right place
   * for that arithmetic.
   */
  apiLogMetrics = (filters: ApiLogFilters) =>
    this.request<unknown>(`/v1/developer/api-logs/metrics${apiLogQuery(filters)}`).then(
      normalizeApiMetrics,
    )

  /** One page of request logs. Paging is keyset via `next_cursor`. */
  apiLogs = (filters: ApiLogFilters) =>
    this.request<unknown>(`/v1/developer/api-logs${apiLogQuery(filters)}`).then(
      normalizeApiLogList,
    )

  /** One request's detail, including the derivation status sentence. */
  apiLogDetail = (eventId: string) =>
    this.request<unknown>(`/v1/developer/api-logs/${encodeURIComponent(eventId)}`).then(
      normalizeApiLogDetail,
    )

  /**
   * Live metric updates over SSE.
   *
   * Read through `fetch` rather than `EventSource`: `EventSource` cannot send an
   * `Authorization` header, and putting the bearer token in the query string —
   * the only alternative it allows — would leak a credential into browser
   * history, proxy logs and `Referer` headers. This is the same reader the job
   * progress stream uses.
   *
   * Returns a cleanup function that aborts the stream; the caller must invoke it
   * on unmount, on disabling Live, and on a session change.
   */
  subscribeApiMetrics(
    filters: ApiLogFilters,
    handlers: {
      onMetrics: (metrics: ApiMetricsResponse) => void
      onError: (message: string) => void
      onOpen?: () => void
    },
  ): () => void {
    const controller = new AbortController()
    const token = this.token

    void (async () => {
      try {
        const res = await fetch(
          `${API_BASE}/v1/developer/api-logs/stream${apiLogQuery(filters)}`,
          {
            headers: {
              Accept: 'text/event-stream',
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            signal: controller.signal,
          },
        )
        if (!res.ok || !res.body) {
          handlers.onError(
            res.status === 503
              ? 'Too many live connections. Close another dashboard or try again shortly.'
              : `Live stream unavailable (${res.status})`,
          )
          return
        }
        handlers.onOpen?.()

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const blocks = buffer.split('\n\n')
          buffer = blocks.pop() ?? ''
          for (const block of blocks) {
            let eventName = 'message'
            const dataLines: string[] = []
            for (const line of block.split('\n')) {
              if (line.startsWith('event:')) eventName = line.slice(6).trim()
              else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
            }
            if (!dataLines.length) continue
            if (eventName === 'metrics') {
              try {
                handlers.onMetrics(normalizeApiMetrics(JSON.parse(dataLines.join('\n'))))
              } catch {
                /* a single malformed frame must not end a healthy stream */
              }
            } else if (eventName === 'error') {
              try {
                handlers.onError(
                  (JSON.parse(dataLines.join('\n')) as { message?: string }).message ??
                    'Live metrics unavailable.',
                )
              } catch {
                handlers.onError('Live metrics unavailable.')
              }
            }
          }
        }
      } catch (err) {
        // An abort is the component unmounting or Live being switched off.
        if ((err as Error).name !== 'AbortError') {
          handlers.onError((err as Error).message)
        }
      }
    })()

    return () => controller.abort()
  }

  // ---- Developer: MCP Activity -------------------------------------------

  /** The account's authorized AI-agent connections. */
  mcpConnections = (range: ApiLogRange) =>
    this.request<unknown>(`/v1/developer/mcp/connections?range=${range}`).then(
      normalizeMcpConnections,
    )

  /** Header counts for the MCP Activity page. */
  mcpActivitySummary = (range: ApiLogRange) =>
    this.request<unknown>(`/v1/developer/mcp/summary?range=${range}`).then(normalizeMcpSummary)

  /** The MCP tool-call log. */
  mcpActivity = (filters: McpActivityFilters) =>
    this.request<unknown>(`/v1/developer/mcp/activity${mcpActivityQuery(filters)}`).then(
      normalizeMcpActivityList,
    )

  /**
   * Pause / resume / revoke a connection.
   *
   * All three return the **authoritative** connection from the server, so the
   * UI updates from the confirmed state instead of from an optimistic guess —
   * a control that only looks applied is worse than one that visibly failed.
   */
  pauseMcpConnection = (id: string) =>
    this.request<unknown>(
      `/v1/developer/mcp/connections/${encodeURIComponent(id)}/pause`,
      { method: 'POST' },
    ).then(normalizeMcpControl)

  resumeMcpConnection = (id: string) =>
    this.request<unknown>(
      `/v1/developer/mcp/connections/${encodeURIComponent(id)}/resume`,
      { method: 'POST' },
    ).then(normalizeMcpControl)

  revokeMcpConnection = (id: string) =>
    this.request<unknown>(
      `/v1/developer/mcp/connections/${encodeURIComponent(id)}/revoke`,
      { method: 'POST' },
    ).then(normalizeMcpControl)
}

/**
 * Build the query string for an API Logs request.
 *
 * Only defined values are sent, so an unset filter is *absent* rather than sent
 * as the literal string `"undefined"` — which the server would treat as a real
 * filter and return nothing for.
 */
function apiLogQuery(filters: ApiLogFilters): string {
  const params = new URLSearchParams({ range: filters.range })
  if (filters.apiKeyId) params.set('api_key_id', filters.apiKeyId)
  if (filters.method) params.set('method', filters.method)
  if (filters.status) params.set('status', filters.status)
  if (filters.route) params.set('route', filters.route)
  if (filters.requestId) params.set('request_id', filters.requestId)
  if (filters.outcome) params.set('outcome', filters.outcome)
  if (filters.cursor) params.set('cursor', filters.cursor)
  if (filters.limit !== undefined) params.set('limit', String(filters.limit))
  return `?${params.toString()}`
}

function mcpActivityQuery(filters: McpActivityFilters): string {
  const params = new URLSearchParams({ range: filters.range })
  if (filters.connectionId) params.set('connection_id', filters.connectionId)
  if (filters.toolName) params.set('tool_name', filters.toolName)
  if (filters.outcome) params.set('outcome', filters.outcome)
  if (filters.cursor) params.set('cursor', filters.cursor)
  if (filters.limit !== undefined) params.set('limit', String(filters.limit))
  return `?${params.toString()}`
}

/**
 * Shared SSE stream reader: fetches a URL, parses `text/event-stream` frames,
 * and dispatches the parsed events to the handlers. Returns a cleanup function
 * that aborts the in-flight stream. Used by both the authed and guest flows
 * (the only differences are the URL and the request headers).
 */
function subscribeToJobStream(
  url: string,
  handlers: {
    onProgress: (evt: import('./types').JobProgressEvent) => void
    onError: (msg: string) => void
    onDone: () => void
    onConnected?: () => void
  },
  headers: Record<string, string> = {},
): () => void {
  const controller = new AbortController()

  void (async () => {
    try {
      const res = await fetch(url, {
        headers,
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        handlers.onError(`Failed to connect (${res.status})`)
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      const dispatch = (eventName: string, dataStr: string) => {
        if (eventName === 'progress') {
          try {
            // Normalised, not raw: a frame that crossed Redis can carry its
            // numbers as strings, which the progress bar would read as "no
            // value" (indeterminate) instead of tracking the job.
            handlers.onProgress(normalizeJobProgressEvent(JSON.parse(dataStr)))
          } catch {
            /* ignore malformed progress */
          }
        } else if (eventName === 'error') {
          // The backend sends `data: {"error": "..."}` — surface the message.
          try {
            const parsed = JSON.parse(dataStr) as { error?: string }
            handlers.onError(parsed.error ?? dataStr)
          } catch {
            handlers.onError(dataStr)
          }
        } else if (eventName === 'connected') {
          handlers.onConnected?.()
        }
      }

      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // SSE events separated by blank lines
        const blocks = buffer.split('\n\n')
        buffer = blocks.pop() ?? ''
        for (const block of blocks) {
          let eventName = 'message'
          const dataLines: string[] = []
          for (const line of block.split('\n')) {
            if (line.startsWith('event:')) eventName = line.slice(6).trim()
            else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
          }
          if (dataLines.length) dispatch(eventName, dataLines.join('\n'))
        }
      }
      handlers.onDone()
    } catch (err) {
      if ((err as Error).name !== 'AbortError') handlers.onError((err as Error).message)
    }
  })()

  return () => controller.abort()
}

/**
 * Download filename for a converted job.
 *
 * Prefers the extension of the object the worker actually produced
 * (`output_file`), because a converter may emit a *container* instead of the
 * target format — e.g. a multi-page `pdf -> png` job produces a `.zip` holding
 * one image per page. Naming that download `<name>.png` would silently hand the
 * user a corrupt file. Falls back to the target format when the job carries no
 * output key yet (e.g. a guest payload before completion).
 */
export function jobOutputFilename(job: {
  input_file?: string | null
  target_format: string
  output_file?: string | null
}): string {
  const base = job.input_file?.split('/').pop()?.replace(/\.[^.]+$/, '') || 'converted'
  const produced = job.output_file?.split('/').pop() ?? ''
  const dot = produced.lastIndexOf('.')
  const extension = dot > 0 ? produced.slice(dot + 1).toLowerCase() : ''
  return `${base}.${extension || job.target_format}`
}

export const api = new ApiClient()
export { API_BASE }

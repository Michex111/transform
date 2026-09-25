import { downloadFromUrl, isTrustedDownloadUrl, saveBlob } from '@/lib/download'
import { encryptFileToFencr, fencrDataKeyToBase64 } from '@/lib/fencr'
import { joinValidationMessages, validationErrorsFrom } from '@/lib/apiErrors'
import {
  normalizeApiKeyCreate,
  normalizeApiKeyList,
  normalizeBatchDelete,
  normalizeCancelSubscription,
  normalizeCheckout,
  normalizeConversionHistory,
  normalizeConversionJob,
  normalizeConversionMap,
  normalizeCreditBalance,
  normalizeCreditHistory,
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
  normalizePhoneStatus,
  normalizePortal,
  normalizePresignedUrls,
  normalizeResendVerification,
  normalizeResetPassword,
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
import type {
  APIKeyCreateRequest,
  BatchDeleteRequest,
  ChangePasswordRequest,
  ConversionJobResponse,
  CreditPurchaseRequest,
  CreateConversionJobRequest,
  CreateLibraryConversionRequest,
  CreateUploadSessionRequest,
  DeleteAccountRequest,
  FavoriteFileRequest,
  ForgotPasswordRequest,
  GuestJobResponse,
  HistoryDeleteRange,
  PresignedUrlsRequest,
  RefreshTokenRequest,
  RequestPhoneVerificationRequest,
  ResetPasswordRequest,
  TokenResponse,
  UpdateProfileRequest,
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

class ApiClient {
  private get token() {
    return localStorage.getItem(TOKEN_KEY)
  }

  private get refreshToken() {
    return localStorage.getItem(REFRESH_KEY)
  }

  setTokens(auth: TokenResponse) {
    localStorage.setItem(TOKEN_KEY, auth.access_token)
    if (auth.refresh_token) localStorage.setItem(REFRESH_KEY, auth.refresh_token)
  }

  clearTokens() {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_KEY)
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

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
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

  me = () => this.request<unknown>('/users/me').then(normalizeUser)

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
  dashboard = () => this.request<unknown>('/v1/user/dashboard').then(normalizeDashboard)
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
  /** Paginated conversion history for the current user, optionally time-ranged. */
  conversionHistory = (page = 1, pageSize = 20, range?: string) =>
    this.request<unknown>(
      `/conversions/history?page=${page}&page_size=${pageSize}${range ? `&range=${encodeURIComponent(range)}` : ''}`,
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
   * Fetch a completed conversion's output bytes as a Blob.
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
   */
  async fetchConversionOutputBlob(jobId: string): Promise<Blob> {
    const job = await this.getJob(jobId)
    const url = job.download_url ?? this.getJobDownloadUrl(jobId)

    if (isTrustedDownloadUrl(url)) {
      const res = await fetch(url)
      if (!res.ok) throw new Error(`Could not read that file (${res.status})`)
      return res.blob()
    }

    // A relative streaming path is used as-is; an absolute URL we refuse to
    // hand to the browser falls back to the job's streaming endpoint.
    const path = ABSOLUTE_URL.test(url) ? this.getJobDownloadUrl(jobId) : url
    const res = await this.authedFetch(resolveServerPath(path))
    if (!res.ok) throw new Error(`Could not read that file (${res.status})`)
    return res.blob()
  }

  // ---- Files ----
  listFolders = () => this.request<unknown>('/v1/files/folders').then(normalizeFolderList)
  /** Get the subfolders + files directly inside a folder. */
  getFolderContents = (folderId: string) =>
    this.request<unknown>(`/v1/files/folders/${folderId}`).then(normalizeFolderContents)
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
  listFiles = (folderId?: string) =>
    this.request<unknown>(`/v1/files${folderId ? `?folder_id=${encodeURIComponent(folderId)}` : ''}`).then(
      normalizeFileList,
    )
  getFile = (id: string) => this.request<unknown>(`/v1/files/${id}`).then(normalizeFile)
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
  creditBalance = () => this.request<unknown>('/v1/credits/balance').then(normalizeCreditBalance)
  creditHistory = () => this.request<unknown>('/v1/credits/history').then(normalizeCreditHistory)
  purchaseCredits = (amount: number) =>
    this.request<unknown>('/v1/credits/purchase', {
      method: 'POST',
      body: JSON.stringify({ amount } satisfies CreditPurchaseRequest),
    }).then(normalizeCheckout)
  creditPricing = () => this.request<unknown>('/v1/credits/pricing').then(normalizeCreditPricing)
  // ---- Subscription ----
  subscriptionPlans = () =>
    this.request<unknown>('/v1/subscription/plans').then(normalizeSubscriptionPlans)
  subscriptionStatus = () =>
    this.request<unknown>('/v1/subscription/status').then(normalizeSubscriptionStatus)
  /** Open a Stripe Customer Portal session for self-service billing management. */
  createPortalSession = () =>
    this.request<unknown>('/v1/subscription/portal', { method: 'POST' }).then(normalizePortal)
  checkout = (tier: string) =>
    this.request<unknown>('/v1/subscription/checkout', {
      method: 'POST',
      body: JSON.stringify({ tier }),
    }).then(normalizeCheckout)
  cancelSubscription = () =>
    this.request<unknown>('/v1/subscription/cancel', { method: 'POST' }).then(
      normalizeCancelSubscription,
    )

  // ---- API Keys ----
  createApiKey = (body: APIKeyCreateRequest) =>
    this.request<unknown>('/v1/api-keys', { method: 'POST', body: JSON.stringify(body) }).then(
      normalizeApiKeyCreate,
    )
  listApiKeys = () => this.request<unknown>('/v1/api-keys').then(normalizeApiKeyList)
  deleteApiKey = (id: string) => this.request<void>(`/v1/api-keys/${id}`, { method: 'DELETE' })

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
            handlers.onProgress(JSON.parse(dataStr))
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

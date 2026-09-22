import { downloadFromUrl, saveBlob } from '@/lib/download'
import { encryptFileToFencr, fencrDataKeyToBase64 } from '@/lib/fencr'
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
  normalizeSubscriptionPlans,
  normalizeSubscriptionStatus,
  normalizeSupportedConversions,
  normalizeTokenResponse,
  normalizeUploadResponse,
  normalizeUploadSession,
  normalizeUser,
  normalizeVerifyEmail,
} from './normalize'
import type {
  APIKeyCreateRequest,
  BatchDeleteRequest,
  ConversionJobResponse,
  CreditPurchaseRequest,
  CreateConversionJobRequest,
  CreateLibraryConversionRequest,
  CreateUploadSessionRequest,
  FavoriteFileRequest,
  GuestJobResponse,
  PresignedUrlsRequest,
  RefreshTokenRequest,
  TokenResponse,
  UserCreateRequest,
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

  constructor(message: string, status: number, code?: string, details?: Record<string, unknown>) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
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
 *   - an array of `{ msg }` objects for 422 request-validation failures.
 *
 * Reading only the string shape turned the structured case into the literal
 * text `"[object Object]"`, which is how this was found.
 */
async function readErrorBody(
  response: Response,
  fallback: string,
): Promise<{ detail: string; code?: string; details?: Record<string, unknown> }> {
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
    const messages = detail
      .map((entry) => (entry as { msg?: unknown } | null)?.msg)
      .filter((msg): msg is string => typeof msg === 'string')
    if (messages.length) return { detail: messages.join(', ') }
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
    // FastAPI will reject the body with HTTP 422.
    if (options.body != null && !headers['Content-Type']) {
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
      const { detail, code, details } = await readErrorBody(res, res.statusText)
      throw new ApiError(detail, res.status, code, details)
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
      const { detail, code, details } = await readErrorBody(res, 'Invalid credentials')
      throw new ApiError(detail, res.status, code, details)
    }
    const data = normalizeTokenResponse(await res.json())
    // Persist tokens immediately so subsequent calls are authenticated.
    this.setTokens(data)
    return data
  }

  me = () => this.request<unknown>('/users/me').then(normalizeUser)

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

  verifyUpload = (id: string, jobId?: string) =>
    this.request<unknown>(
      `/uploads/sessions/${id}/verify${jobId ? `?job_id=${encodeURIComponent(jobId)}` : ''}`,
      { method: 'POST' },
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
    await this.putToPresignedUrl(upload.upload_url, uploadBytes)
    // 4. Verify upload completion and enqueue the job.
    await this.verifyUpload(upload.upload_id, job.job_id)
    return job
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
    await this.guestPutToPresignedUrl(upload.upload_url, uploadBytes)
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

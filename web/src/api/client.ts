import { downloadFromUrl, saveBlob } from '@/lib/download'
import type {
  APIKeyCreateRequest,
  APIKeyCreateResponse,
  APIKeyListResponse,
  BatchDeleteRequest,
  BatchDeleteResponse,
  CancelSubscriptionResponse,
  CheckoutResponse,
  ConversionHistoryResponse,
  ConversionJobResponse,
  ConversionMapResponse,
  CreditBalanceResponse,
  CreditPricingResponse,
  CreditPurchaseRequest,
  CreditTransactionResponse,
  CreateConversionJobRequest,
  CreateLibraryConversionRequest,
  CreateUploadSessionRequest,
  DashboardResponse,
  FavoriteFileRequest,
  FileDownloadResponse,
  FileListResponse,
  FileMetadataResponse,
  FolderContentsResponse,
  FolderListResponse,
  FolderResponse,
  PortalResponse,
  PresignedUrlResponse,
  PresignedUrlsRequest,
  RefreshTokenRequest,
  SubscriptionPlanResponse,
  SubscriptionStatusResponse,
  SupportedConversion,
  TokenResponse,
  UploadResponse,
  UploadSession,
  UserCreateRequest,
  UserResponse,
} from './types'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '')
const TOKEN_KEY = 'transform_access_token'
const REFRESH_KEY = 'transform_refresh_token'

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
      const data = (await res.json()) as TokenResponse
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
      let detail = res.statusText
      try {
        const body = await res.json()
        detail = Array.isArray(body.detail) ? body.detail.map((d: unknown) => String((d as { msg?: string }).msg ?? d)).join(', ') : (body.detail ?? detail)
      } catch {
        /* ignore */
      }
      throw new Error(detail)
    }

    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  }

  // ---- Auth ----
  register = (body: UserCreateRequest) =>
    this.request<UserResponse>('/users/register', { method: 'POST', body: JSON.stringify(body) })

  login = async (username: string, password: string) => {
    const form = new URLSearchParams({ username, password })
    const res = await fetch(`${API_BASE}/users/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({}))
      throw new Error(body.detail ?? 'Invalid credentials')
    }
    const data = (await res.json()) as TokenResponse
    // Persist tokens immediately so subsequent calls are authenticated.
    this.setTokens(data)
    return data
  }

  me = () => this.request<UserResponse>('/users/me')

  // ---- Dashboard ----
  dashboard = () => this.request<DashboardResponse>('/v1/user/dashboard')
  profile = () => this.request<UserResponse>('/v1/user/profile')

  // ---- Upload (presigned URL flow) ----
  createUploadSession = (body: CreateUploadSessionRequest) =>
    this.request<UploadResponse>('/uploads/sessions', { method: 'POST', body: JSON.stringify(body) })

  getUploadSession = (id: string) => this.request<UploadSession>(`/uploads/sessions/${id}`)

  verifyUpload = (id: string, jobId?: string) =>
    this.request<UploadSession>(
      `/uploads/sessions/${id}/verify${jobId ? `?job_id=${encodeURIComponent(jobId)}` : ''}`,
      { method: 'POST' },
    )

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
  supportedConversions = () => this.request<SupportedConversion[]>('/conversions/supported')
  /** Map of source format -> valid target formats. */
  conversionMap = () => this.request<ConversionMapResponse>('/conversions/supported/map')
  createConversion = (body: CreateConversionJobRequest) =>
    this.request<ConversionJobResponse>('/conversions/jobs', { method: 'POST', body: JSON.stringify(body) })
  /**
   * Convert a file that already exists in the user's library (object storage).
   * The server infers the source format from the stored file's extension and
   * enqueues the job immediately.
   */
  convertLibraryFile = (fileId: string, targetFormat: string) =>
    this.request<ConversionJobResponse>('/conversions/jobs', {
      method: 'POST',
      body: JSON.stringify({ file_id: fileId, target_format: targetFormat } satisfies CreateLibraryConversionRequest),
    })
  getJob = (id: string) => this.request<ConversionJobResponse>(`/conversions/jobs/${id}`)
  /** Paginated conversion history for the current user, optionally time-ranged. */
  conversionHistory = (page = 1, pageSize = 20, range?: string) =>
    this.request<ConversionHistoryResponse>(
      `/conversions/history?page=${page}&page_size=${pageSize}${range ? `&range=${encodeURIComponent(range)}` : ''}`,
    )
  /** Delete a single history record owned by the current user. */
  deleteHistoryJob = (id: string) =>
    this.request<void>(`/conversions/history/${id}`, { method: 'DELETE' })
  /** Retry a failed job without re-uploading its input file. */
  retryJob = (id: string) =>
    this.request<ConversionJobResponse>(`/conversions/jobs/${id}/retry`, { method: 'POST' })

  /**
   * Run the full "normal conversion" flow with a file: create the job, open an
   * upload session, PUT the bytes to the presigned URL, then verify/enqueue.
   * Used for the retry fallback when a job's input object is gone from storage.
   */
  async convertWithFile(
    source: string,
    target: string,
    file: Blob,
    fileName?: string,
  ): Promise<ConversionJobResponse> {
    const name = fileName || (file instanceof File ? file.name : 'file')
    // 1. Create the conversion job.
    const job = await this.createConversion({
      source_format: source,
      target_format: target,
      input_key: name,
    })
    // 2. Create an upload session.
    const upload = await this.createUploadSession({ file_extension: source, file_name: name })
    // 3. Upload bytes directly to the presigned URL (no Content-Type header —
    //    the URL is signed without one, so sending it would 403 on B2/S3).
    await this.putToPresignedUrl(upload.upload_url, file)
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
    const url = job.download_url

    if (url.startsWith('http')) {
      // Pre-signed GET URL — no auth header needed, download directly.
      downloadFromUrl(url, filename ?? defaultJobFilename(job))
    } else {
      // Same-origin streaming endpoint — requires the Authorization header.
      const res = await fetch(`${API_BASE}${url}`, {
        headers: this.token ? { Authorization: `Bearer ${this.token}` } : {},
      })
      if (!res.ok) throw new Error(`Download failed (${res.status})`)
      const blob = await res.blob()
      saveBlob(blob, filename ?? defaultJobFilename(job))
    }
  }

  // ---- Files ----
  listFolders = () => this.request<FolderListResponse>('/v1/files/folders')
  /** Get the subfolders + files directly inside a folder. */
  getFolderContents = (folderId: string) =>
    this.request<FolderContentsResponse>(`/v1/files/folders/${folderId}`)
  createFolder = (name: string, parentId?: string | null) =>
    this.request<FolderResponse>('/v1/files/folders', {
      method: 'POST',
      body: JSON.stringify({ name, parent_id: parentId ?? null }),
    })
  renameFolder = (id: string, name: string) =>
    this.request<FolderResponse>(`/v1/files/folders/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    })
  deleteFolder = (id: string) => this.request<void>(`/v1/files/folders/${id}`, { method: 'DELETE' })
  listFiles = (folderId?: string) =>
    this.request<FileListResponse>(`/v1/files${folderId ? `?folder_id=${encodeURIComponent(folderId)}` : ''}`)
  getFile = (id: string) => this.request<FileMetadataResponse>(`/v1/files/${id}`)
  deleteFile = (id: string) => this.request<void>(`/v1/files/${id}`, { method: 'DELETE' })
  renameFile = (id: string, name: string) =>
    this.request<FileMetadataResponse>(`/v1/files/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    })
  moveFile = (id: string, folderId: string | null) =>
    this.request<FileMetadataResponse>(`/v1/files/${id}/move`, {
      method: 'POST',
      body: JSON.stringify({ folder_id: folderId }),
    })
  getFileDownload = (id: string) => this.request<FileDownloadResponse>(`/v1/files/${id}/download`)
  getPresignedUrls = (body: PresignedUrlsRequest) =>
    this.request<PresignedUrlResponse[]>('/v1/files/urls', { method: 'POST', body: JSON.stringify(body) })
  /** Set whether a file is a favorite (pinned to the favorites view). */
  setFileFavorite = (id: string, isFavorite: boolean) =>
    this.request<FileMetadataResponse>(`/v1/files/${id}/favorite`, {
      method: 'PATCH',
      body: JSON.stringify({ is_favorite: isFavorite } satisfies FavoriteFileRequest),
    })
  /** Paginated list of the user's favorited files. */
  listFavorites = (page = 1, pageSize = 50) =>
    this.request<FileListResponse>(`/v1/files/favorites?page=${page}&page_size=${pageSize}`)
  /** Delete multiple files and/or folders in one request. */
  batchDelete = (body: BatchDeleteRequest) =>
    this.request<BatchDeleteResponse>('/v1/files/batch-delete', {
      method: 'POST',
      body: JSON.stringify(body),
    })
  /** Move a folder under a new parent (pass null to move to the root). */
  moveFolder = (id: string, parentId: string | null) =>
    this.request<FolderResponse>(`/v1/files/folders/${id}/move`, {
      method: 'POST',
      body: JSON.stringify({ parent_id: parentId }),
    })

  // ---- Credits ----
  creditBalance = () => this.request<CreditBalanceResponse>('/v1/credits/balance')
  creditHistory = () => this.request<CreditTransactionResponse[]>('/v1/credits/history')
  purchaseCredits = (amount: number) =>
    this.request<CheckoutResponse>('/v1/credits/purchase', {
      method: 'POST',
      body: JSON.stringify({ amount } satisfies CreditPurchaseRequest),
    })
  creditPricing = () => this.request<CreditPricingResponse[]>('/v1/credits/pricing')

  // ---- Subscription ----
  subscriptionPlans = () => this.request<SubscriptionPlanResponse[]>('/v1/subscription/plans')
  subscriptionStatus = () => this.request<SubscriptionStatusResponse>('/v1/subscription/status')
  /** Open a Stripe Customer Portal session for self-service billing management. */
  createPortalSession = () =>
    this.request<PortalResponse>('/v1/subscription/portal', { method: 'POST' })
  checkout = (tier: string) =>
    this.request<CheckoutResponse>('/v1/subscription/checkout', {
      method: 'POST',
      body: JSON.stringify({ tier }),
    })
  cancelSubscription = () =>
    this.request<CancelSubscriptionResponse>('/v1/subscription/cancel', { method: 'POST' })

  // ---- API Keys ----
  createApiKey = (body: APIKeyCreateRequest) =>
    this.request<APIKeyCreateResponse>('/v1/api-keys', { method: 'POST', body: JSON.stringify(body) })
  listApiKeys = () => this.request<APIKeyListResponse>('/v1/api-keys')
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
    const controller = new AbortController()
    const token = this.token

    void (async () => {
      try {
        const res = await fetch(`${API_BASE}/v1/events/jobs/${id}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
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
}

/** Build a sensible output filename for a completed job's download. */
function defaultJobFilename(job: ConversionJobResponse): string {
  const base = job.input_file.split('/').pop()?.replace(/\.[^.]+$/, '') || 'converted'
  return `${base}.${job.target_format}`
}

export const api = new ApiClient()
export { API_BASE }

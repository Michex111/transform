import type {
  APIKeyCreateRequest,
  APIKeyCreateResponse,
  APIKeyListResponse,
  CancelSubscriptionResponse,
  CheckoutResponse,
  ConversionJobResponse,
  CreditBalanceResponse,
  CreditPricingResponse,
  CreditPurchaseRequest,
  CreditTransactionResponse,
  CreateConversionJobRequest,
  CreateUploadSessionRequest,
  DashboardResponse,
  FileDownloadResponse,
  FileListResponse,
  FileMetadataResponse,
  FolderListResponse,
  FolderResponse,
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

  /** Upload file bytes directly to the presigned URL (no auth header needed). */
  async putToPresignedUrl(url: string, blob: Blob, contentType: string): Promise<void> {
    const res = await fetch(url, { method: 'PUT', body: blob, headers: { 'Content-Type': contentType } })
    if (!res.ok) throw new Error(`Upload failed (${res.status})`)
  }

  // ---- Conversions ----
  supportedConversions = () => this.request<SupportedConversion[]>('/conversions/supported')
  createConversion = (body: CreateConversionJobRequest) =>
    this.request<ConversionJobResponse>('/conversions/jobs', { method: 'POST', body: JSON.stringify(body) })
  getJob = (id: string) => this.request<ConversionJobResponse>(`/conversions/jobs/${id}`)
  getJobDownloadUrl = (id: string) => `/conversions/jobs/${id}/download`

  // ---- Files ----
  listFolders = () => this.request<FolderListResponse>('/v1/files/folders')
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
  moveFile = (id: string, folderId: string | null) =>
    this.request<FileMetadataResponse>(`/v1/files/${id}/move`, {
      method: 'POST',
      body: JSON.stringify({ folder_id: folderId }),
    })
  getFileDownload = (id: string) => this.request<FileDownloadResponse>(`/v1/files/${id}/download`)
  getPresignedUrls = (body: PresignedUrlsRequest) =>
    this.request<PresignedUrlResponse[]>('/v1/files/urls', { method: 'POST', body: JSON.stringify(body) })

  // ---- Credits ----
  creditBalance = () => this.request<CreditBalanceResponse>('/v1/credits/balance')
  creditHistory = () => this.request<CreditTransactionResponse[]>('/v1/credits/history')
  purchaseCredits = (amount: number) =>
    this.request<CreditTransactionResponse>('/v1/credits/purchase', {
      method: 'POST',
      body: JSON.stringify({ amount } satisfies CreditPurchaseRequest),
    })
  creditPricing = () => this.request<CreditPricingResponse[]>('/v1/credits/pricing')

  // ---- Subscription ----
  subscriptionPlans = () => this.request<SubscriptionPlanResponse[]>('/v1/subscription/plans')
  subscriptionStatus = () => this.request<SubscriptionStatusResponse>('/v1/subscription/status')
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

export const api = new ApiClient()
export { API_BASE }

// ---- Auth ----
export interface UserCreateRequest {
  username: string
  email: string
  password: string
}

export interface UserResponse {
  id: number
  username: string
  email: string
  is_active: boolean
  created_at: string
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

export interface StorageStats {
  used_bytes: number
  limit_bytes: number
  used_percent: number
  file_count: number
}

// ---- Upload ----
export interface CreateUploadSessionRequest {
  file_extension: string
  file_name?: string
  folder_id?: string | null
}

export interface UploadResponse {
  upload_id: string
  object_key: string
  upload_url: string
  expires_in_minutes: number
}

export interface UploadSession {
  upload_id: string
  object_key: string
  status: string
  file_name: string | null
  folder_id: string | null
}

// ---- Conversions ----
export interface CreateConversionJobRequest {
  source_format: string
  target_format: string
  input_key: string
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
}

/** Paginated list of a user's conversion history. */
export interface ConversionHistoryResponse {
  jobs: ConversionJobResponse[]
  total: number
  page: number
  page_size: number
}

export interface SupportedConversion {
  source_format: string
  target_format: string
}

export interface ConversionMapResponse {
  conversions: Record<string, string[]>
}

/** Map of every supported source format to its valid target formats. */
export interface ConversionMap {
  conversions: Record<string, string[]>
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
}

export interface JobErrorEvent {
  error: string
}

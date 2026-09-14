/**
 * Client-side file encryption ("FENCR" v1) for the Transform file-conversion app.
 *
 * The browser encrypts a file BEFORE upload so the server never sees the
 * plaintext. The Python backend decrypts the resulting blob (see
 * `src/infrastructure/adapters/security/encryption.py`), and this module must
 * produce a **byte-identical** blob — verified by the golden-vector test in
 * `fencr.test.ts` (mirror of `tests/unit/infrastructure/test_encryption.py`).
 *
 * ## FENCR v1 byte format (exact)
 * ```
 * header = MAGIC("FENCR" = 46 45 4E 43 52, 5 bytes)
 *        + VERSION(\x01, 1 byte)
 *        + chunk_size (4 bytes, big-endian uint32)
 *        + salt (16 bytes)
 *        + nonce_prefix (8 bytes)                 // total header = 34 bytes
 * blob   = header + for each chunk: AES-256-GCM ciphertext (chunk_len + 16 tag)
 * ```
 *
 * ## Per-chunk crypto (WebCrypto AES-GCM)
 * ```
 * file_key = HKDF-SHA256(input = data_key, salt = salt, info = "transform-client-v1:enc", len = 32)
 * aad      = "transform-client-v1"
 * nonce    = nonce_prefix || counter.toBytesBE(4)   // counter = 0-based chunk index
 * ```
 *
 * ## Streaming
 * The file is never loaded into memory as one buffer. It is read chunk-by-chunk
 * in `chunkSize` slices, each encrypted in place, so peak memory is one chunk
 * (plus the accumulated ciphertext parts assembled into the final `Blob`).
 */

/** A Uint8Array backed by a real ArrayBuffer (not SharedArrayBuffer). */
export type Bytes = Uint8Array<ArrayBuffer>;

export const FENCR_MAGIC: Bytes = new Uint8Array([0x46, 0x45, 0x4e, 0x43, 0x52]); // "FENCR"
const FENCR_VERSION = 0x01;
const SALT_LENGTH = 16;
const NONCE_PREFIX_LENGTH = 8;
const GCM_NONCE_LENGTH = NONCE_PREFIX_LENGTH + 4; // 12 bytes
const HEADER_LENGTH = FENCR_MAGIC.length + 1 + 4 + SALT_LENGTH + NONCE_PREFIX_LENGTH; // 34

const FENCR_HKDF_INFO = "transform-client-v1:enc";
const FENCR_AAD = "transform-client-v1";

/** Default chunk size (1 MiB) — stored in the header so any size is valid. */
export const DEFAULT_FENCR_CHUNK_SIZE = 1024 * 1024;

export interface FencrResult {
  /** The complete FENCR blob, ready to PUT to object storage. */
  encrypted: Blob;
  /** The fresh 32-byte per-file data key — base64 it and send to the backend. */
  dataKey: Bytes;
}

export interface EncryptFileOptions {
  /** Chunk size (bytes) the file is split into before per-chunk AES-GCM. */
  chunkSize?: number;
}

/** Encode raw bytes as a base64 string (browser + Node safe). */
export function bytesToBase64(bytes: Bytes): string {
  let binary = "";
  // String.fromCharCode bounds: apply in slices to avoid argument overflow.
  const slice = 0x8000;
  for (let i = 0; i < bytes.length; i += slice) {
    binary += String.fromCharCode(...bytes.subarray(i, i + slice));
  }
  return btoa(binary);
}

/** Base64 of a raw (unwrapped) 32-byte FENCR data key, for the API body. */
export function fencrDataKeyToBase64(dataKey: Bytes): string {
  return bytesToBase64(dataKey);
}

/** Base64 of a WebCrypto key's raw bytes (the key MUST be extractable). */
export async function webcryptoKeyToBase64(key: CryptoKey): Promise<string> {
  const raw = await crypto.subtle.exportKey("raw", key);
  return bytesToBase64(new Uint8Array(raw));
}

/** Derive the 32-byte per-file AES-256-GCM key from the raw data key + salt. */
async function deriveFileKey(dataKey: Bytes, salt: Bytes): Promise<ArrayBuffer> {
  const hkdfKey = await crypto.subtle.importKey("raw", dataKey, { name: "HKDF" }, false, [
    "deriveBits",
  ]);
  return crypto.subtle.deriveBits(
    { name: "HKDF", hash: "SHA-256", salt, info: new TextEncoder().encode(FENCR_HKDF_INFO) },
    hkdfKey,
    256,
  );
}

/** Assemble the 34-byte FENCR v1 header. */
function buildHeader(chunkSize: number, salt: Bytes, noncePrefix: Bytes): Bytes {
  const header = new Uint8Array(HEADER_LENGTH);
  header.set(FENCR_MAGIC, 0);
  header[FENCR_MAGIC.length] = FENCR_VERSION;
  // chunk_size, big-endian uint32.
  new DataView(header.buffer).setUint32(FENCR_MAGIC.length + 1, chunkSize, false);
  header.set(salt, FENCR_MAGIC.length + 1 + 4);
  header.set(noncePrefix, FENCR_MAGIC.length + 1 + 4 + SALT_LENGTH);
  return header;
}

/** Build a 12-byte GCM nonce = nonce_prefix || counter (big-endian uint32). */
function buildNonce(noncePrefix: Bytes, counter: number): Bytes {
  const nonce = new Uint8Array(GCM_NONCE_LENGTH);
  nonce.set(noncePrefix, 0);
  new DataView(nonce.buffer).setUint32(NONCE_PREFIX_LENGTH, counter, false);
  return nonce;
}

/**
 * Encrypt fixed inputs into a FENCR blob. This is the byte-level core; the
 * golden-vector test passes fixed salt/nonce/data keys and asserts the exact
 * base64. Production code uses {@link encryptFileToFencr}, which generates
 * fresh salt/nonce/data key internally per file.
 */
export async function fencrEncrypt(
  plaintext: Blob,
  dataKey: Bytes,
  salt: Bytes,
  noncePrefix: Bytes,
  chunkSize: number,
): Promise<Blob> {
  if (!Number.isInteger(chunkSize) || chunkSize <= 0) {
    throw new Error("chunkSize must be a positive integer.");
  }
  if (dataKey.length !== 32) throw new Error("dataKey must be exactly 32 bytes.");
  if (salt.length !== SALT_LENGTH) throw new Error(`salt must be exactly ${SALT_LENGTH} bytes.`);
  if (noncePrefix.length !== NONCE_PREFIX_LENGTH) {
    throw new Error(`noncePrefix must be exactly ${NONCE_PREFIX_LENGTH} bytes.`);
  }

  const fileKey = await deriveFileKey(dataKey, salt);
  const aesKey = await crypto.subtle.importKey("raw", fileKey, { name: "AES-GCM" }, false, [
    "encrypt",
  ]);
  const aad = new TextEncoder().encode(FENCR_AAD);
  const header = buildHeader(chunkSize, salt, noncePrefix);

  const parts: BlobPart[] = [header];
  const size = plaintext.size;
  let counter = 0;

  for (let offset = 0; offset < size; offset += chunkSize) {
    const slice = plaintext.slice(offset, Math.min(offset + chunkSize, size));
    const chunk = new Uint8Array(await new Response(slice).arrayBuffer());
    const nonce = buildNonce(noncePrefix, counter);
    const ciphertext = await crypto.subtle.encrypt(
      { name: "AES-GCM", iv: nonce, additionalData: aad },
      aesKey,
      chunk,
    );
    parts.push(new Uint8Array(ciphertext));
    counter += 1;
  }

  return new Blob(parts);
}

/**
 * Encrypt a file in the browser before upload.
 *
 * Generates a fresh 32-byte data key (plus random salt + nonce prefix) and
 * streams the file through `chunkSize` slices so the whole file is never
 * buffered at once. Returns the FENCR blob (to PUT) and the raw data key (to
 * send base64-encoded to the backend).
 */
export async function encryptFileToFencr(
  file: Blob,
  opts: EncryptFileOptions = {},
): Promise<FencrResult> {
  const chunkSize = opts.chunkSize ?? DEFAULT_FENCR_CHUNK_SIZE;

  // Fresh per-file key material — never reuse across files or attempts.
  const dataKey = crypto.getRandomValues(new Uint8Array(32));
  const salt = crypto.getRandomValues(new Uint8Array(SALT_LENGTH));
  const noncePrefix = crypto.getRandomValues(new Uint8Array(NONCE_PREFIX_LENGTH));

  const encrypted = await fencrEncrypt(file, dataKey, salt, noncePrefix, chunkSize);
  return { encrypted, dataKey };
}

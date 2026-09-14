import { describe, it, expect } from "vitest";
import {
  fencrEncrypt,
  bytesToBase64,
  FENCR_MAGIC,
  type Bytes,
} from "./fencr";

/**
 * Golden-vector test — must match the Python backend's authoritative vector in
 * `tests/unit/infrastructure/test_encryption.py::TestClientSideFencrEncryption`.
 *
 *   data_key     = bytes([0x42]) * 32
 *   salt         = bytes(range(1, 17))
 *   nonce_prefix = bytes([0xAA]) * 8
 *   chunk_size   = 16
 *   plaintext    = b"hello world"
 *
 * The blob produced here is what the Python `decrypt_fencr_bytes` must recover
 * back to `b"hello world"`. Byte-identical is the whole point.
 */
const DATA_KEY: Bytes = new Uint8Array(32).fill(0x42);
const SALT: Bytes = Uint8Array.from([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]);
const NONCE_PREFIX: Bytes = new Uint8Array(8).fill(0xaa);
const CHUNK_SIZE = 16;
const PLAINTEXT_B64 = "aGVsbG8gd29ybGQ="; // base64("hello world")

const EXPECTED_FULL_BLOB_B64 =
  "RkVOQ1IBAAAAEAECAwQFBgcICQoLDA0ODxCqqqqqqqqqqmo21IilUFfrmkg3QRBlt8YA8GfN3r+gZ/Swag==";
const EXPECTED_FILE_KEY_HEX =
  "1ea3caeecd8f537ce8c168328a16890f718c1689145494b1c23f105d0ec8fef5";

function blobToUint8Array(blob: Blob): Promise<Bytes> {
  return blob.arrayBuffer().then((buf) => new Uint8Array(buf));
}

async function fileKeyHex(): Promise<string> {
  // Re-derive the file key independently from the raw data key so we can assert
  // the documented HKDF output in addition to the final blob.
  const hkdfKey = await crypto.subtle.importKey("raw", DATA_KEY, { name: "HKDF" }, false, [
    "deriveBits",
  ]);
  const bits = await crypto.subtle.deriveBits(
    { name: "HKDF", hash: "SHA-256", salt: SALT, info: new TextEncoder().encode("transform-client-v1:enc") },
    hkdfKey,
    256,
  );
  return Array.from(new Uint8Array(bits))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

describe("FENCR golden vector (byte-identical to Python)", () => {
  it("derives the documented file key", async () => {
    expect(await fileKeyHex()).toBe(EXPECTED_FILE_KEY_HEX);
  });

  it("produces the exact golden blob for the fixed inputs", async () => {
    const plaintext = new Blob([Uint8Array.from(atob(PLAINTEXT_B64), (c) => c.charCodeAt(0))]);
    const blob = await fencrEncrypt(plaintext, DATA_KEY, SALT, NONCE_PREFIX, CHUNK_SIZE);
    const bytes = await blobToUint8Array(blob);
    expect(bytesToBase64(bytes)).toBe(EXPECTED_FULL_BLOB_B64);
    expect(bytesToBase64(bytes)).toEqual(EXPECTED_FULL_BLOB_B64);
  });

  it("hard-codes the magic + header prefix exactly", async () => {
    const bytes = await blobToUint8Array(
      await fencrEncrypt(new Blob(), DATA_KEY, SALT, NONCE_PREFIX, CHUNK_SIZE),
    );
    // FENCR magic
    expect(Array.from(bytes.slice(0, 5))).toEqual(Array.from(FENCR_MAGIC));
    // version byte
    expect(bytes[5]).toBe(0x01);
    // Chunk size (big-endian uint32) at offset 6: must be the given chunk size.
    expect(new DataView(bytes.buffer).getUint32(6, false)).toBe(CHUNK_SIZE);
    // Salt at offset 10, nonce prefix at offset 26 — exact bytes from the vector.
    expect(Array.from(bytes.slice(10, 26))).toEqual(Array.from(SALT));
    expect(Array.from(bytes.slice(26, 34))).toEqual(Array.from(NONCE_PREFIX));
    // The header resolves to exactly the golden blob's leading 34 bytes.
    const golden = new Uint8Array(atob(EXPECTED_FULL_BLOB_B64).split("").map((c) => c.charCodeAt(0)));
    expect(Array.from(bytes.slice(0, 34))).toEqual(Array.from(golden.slice(0, 34)));
  });
});

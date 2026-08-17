"""Tests for the encryption service."""

import pytest
from src.infrastructure.adapters.security.encryption import (
    DEFAULT_CHUNK_SIZE,
    FileEncryptionService,
)


class TestFileEncryptionService:
    """Tests for AES-256-GCM file encryption with per-user keys."""

    def test_encrypt_decrypt_roundtrip(self):
        """Encrypt and decrypt should produce the original data."""
        service = FileEncryptionService()
        original = b"Hello, this is secret file content!"
        user_id = "user-123"

        encrypted = service.encrypt_file(original, user_id)
        assert encrypted != original
        assert len(encrypted) > len(original)

        decrypted = service.decrypt_file(encrypted, user_id)
        assert decrypted == original

    def test_different_users_have_different_keys(self):
        """Different users should get different encryption keys."""
        service = FileEncryptionService()
        data = b"test data"

        encrypted_a = service.encrypt_file(data, "user-a")
        encrypted_b = service.encrypt_file(data, "user-b")

        # Ciphertexts should be different because keys differ
        assert encrypted_a != encrypted_b

    def test_cannot_decrypt_with_wrong_user(self):
        """Decrypting with a different user ID should fail."""
        service = FileEncryptionService()
        data = b"secret"

        encrypted = service.encrypt_file(data, "user-a")

        with pytest.raises(Exception):
            service.decrypt_file(encrypted, "user-b")

    def test_master_key_persistence(self):
        """Service should expose its master key."""
        service = FileEncryptionService()
        key = service.master_key
        assert len(key) > 0
        assert isinstance(key, bytes)

    def test_from_base64_key(self):
        """Creating from a base64 key should work."""
        from cryptography.fernet import Fernet

        # Generate a proper Fernet key
        key = Fernet.generate_key()
        key_b64 = key.decode()

        service = FileEncryptionService(master_key=key)

        # Encrypt with the service
        data = b"test"
        encrypted = service.encrypt_file(data, "user-1")

        # Create a new service from the same key
        service2 = FileEncryptionService.from_base64_key(key_b64)
        decrypted = service2.decrypt_file(encrypted, "user-1")
        assert decrypted == data

    def test_empty_data(self):
        """Empty data should still encrypt and decrypt."""
        service = FileEncryptionService()
        encrypted = service.encrypt_file(b"", "user-1")
        decrypted = service.decrypt_file(encrypted, "user-1")
        assert decrypted == b""

    def test_large_data(self):
        """Large data should encrypt and decrypt correctly."""
        service = FileEncryptionService()
        data = b"x" * (1024 * 1024)  # 1MB

        encrypted = service.encrypt_file(data, "user-1")
        decrypted = service.decrypt_file(encrypted, "user-1")
        assert decrypted == data


class TestStreamingEncryption:
    """Tests for the chunked AES-256-GCM streaming format."""

    def _roundtrip(self, data: bytes, user_id: str = "user-1") -> bytes:
        service = FileEncryptionService()
        return service.decrypt_bytes(service.encrypt_bytes(data, user_id), user_id)

    def test_streaming_roundtrip_small(self):
        assert self._roundtrip(b"small payload") == b"small payload"

    def test_streaming_roundtrip_multi_chunk(self):
        # 2.5 MiB forces multiple 1 MiB chunks
        data = bytes(range(256)) * 10240  # ~2.5 MiB
        assert self._roundtrip(data) == data

    def test_streaming_roundtrip_exact_chunk_boundary(self):
        data = b"x" * DEFAULT_CHUNK_SIZE
        assert self._roundtrip(data) == data

    def test_streaming_roundtrip_crosses_chunk_boundary(self):
        data = b"y" * (DEFAULT_CHUNK_SIZE + 1)
        assert self._roundtrip(data) == data

    def test_streaming_empty_file(self):
        assert self._roundtrip(b"") == b""

    def test_streaming_encrypts_at_rest(self):
        service = FileEncryptionService()
        ciphertext = service.encrypt_bytes(b"secret", "user-1")
        # header magic present, plaintext not visible
        assert ciphertext.startswith(b"TRENC")
        assert b"secret" not in ciphertext

    def test_streaming_wrong_user_fails(self):
        service = FileEncryptionService()
        ciphertext = service.encrypt_bytes(b"secret", "user-a")
        with pytest.raises(Exception):
            service.decrypt_bytes(ciphertext, "user-b")

    def test_streaming_tamper_detected(self):
        service = FileEncryptionService()
        ciphertext = bytearray(service.encrypt_bytes(b"attack surface", "user-1"))
        ciphertext[-1] ^= 0xFF  # flip a bit in the final tag/byte
        with pytest.raises(Exception):
            service.decrypt_bytes(bytes(ciphertext), "user-1")

    def test_streaming_rejects_foreign_header(self):
        service = FileEncryptionService()
        with pytest.raises(ValueError, match="Not a Transform-encrypted file"):
            service.decrypt_bytes(b"not-encrypted-data", "user-1")

    def test_streaming_file_path_api_roundtrip(self, tmp_path):
        service = FileEncryptionService()
        plain = tmp_path / "plain.bin"
        encrypted = tmp_path / "plain.bin.enc"
        decrypted = tmp_path / "decrypted.bin"

        plain.write_bytes(b"file based streaming roundtrip" * 1000)

        service.encrypt_file_to(plain, encrypted, "user-1")
        assert encrypted.exists()
        assert plain.read_bytes() not in encrypted.read_bytes()

        service.decrypt_file_to(encrypted, decrypted, "user-1")
        assert decrypted.read_bytes() == plain.read_bytes()

    def test_streaming_reuses_master_key_across_services(self):
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        svc1 = FileEncryptionService(master_key=key)
        svc2 = FileEncryptionService.from_base64_key(key.decode())

        ciphertext = svc1.encrypt_bytes(b"shared key", "user-1")
        assert svc2.decrypt_bytes(ciphertext, "user-1") == b"shared key"

    def test_iter_decrypt_streams_in_chunks(self):
        """iter_decrypt must yield identical plaintext via a reader callback."""
        from src.infrastructure.adapters.security.encryption import DEFAULT_CHUNK_SIZE

        service = FileEncryptionService()
        data = b"streaming reader payload" * 200_000  # ~4.5 MiB, multiple chunks
        ciphertext = service.encrypt_bytes(data, "user-1")

        pos = 0

        def read_n(n: int) -> bytes:
            nonlocal pos
            chunk = ciphertext[pos:pos + n]
            pos += len(chunk)
            return chunk

        decrypted = b"".join(service.iter_decrypt(read_n, "user-1"))
        assert decrypted == data

    def test_iter_decrypt_detects_tamper(self):
        service = FileEncryptionService()
        ciphertext = bytearray(service.encrypt_bytes(b"tamper me", "user-1"))
        ciphertext[-1] ^= 0x01

        pos = 0

        def read_n(n: int) -> bytes:
            nonlocal pos
            chunk = bytes(ciphertext[pos:pos + n])
            pos += len(chunk)
            return chunk

        with pytest.raises(Exception):
            for _ in service.iter_decrypt(read_n, "user-1"):
                pass

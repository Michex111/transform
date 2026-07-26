from src.infrastructure.adapters.storage.minio_object_gateway_adapter import MinioObjectGatewayAdapter


class StubMinioUrlStorageAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def generate_get_url(self, object_key: str, expires_in_minutes: int = 60) -> str:
        self.calls.append((object_key, expires_in_minutes))
        return f"https://storage.test/{object_key}"


def test_minio_object_gateway_adapter_forwards_get_url_generation() -> None:
    storage = StubMinioUrlStorageAdapter()
    adapter = MinioObjectGatewayAdapter(storage)

    url = adapter.generate_get_url("outputs/file.pdf", expires_in_minutes=15)

    assert url == "https://storage.test/outputs/file.pdf"
    assert storage.calls == [("outputs/file.pdf", 15)]

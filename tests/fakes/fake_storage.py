from pathlib import Path


class FakeStoragePort:
    def __init__(self, seed_files: dict[str, bytes] | None = None) -> None:
        self.objects: dict[str, bytes] = dict(seed_files or {})
        self.download_calls: list[tuple[str, Path]] = []
        self.upload_calls: list[tuple[str, Path]] = []

    def download(self, key: str, dest_path: Path) -> None:
        self.download_calls.append((key, dest_path))
        if key not in self.objects:
            raise FileNotFoundError(f"Object not found: {key}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(self.objects[key])

    def upload(self, target_key: str, source_path: Path) -> None:
        self.upload_calls.append((target_key, source_path))
        payload = source_path.read_bytes()
        self.objects[target_key] = payload
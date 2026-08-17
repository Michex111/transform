from pathlib import Path
from typing import Protocol

class FileStorageGateway(Protocol):
    def download(self, key: str, dest_path: Path) -> None: ...

    def upload(self, target_key: str, source_path: Path) -> None: ...







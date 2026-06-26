class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict | None]] = []

    def info(self, message: str, extra: dict | None = None, **_: object) -> None:
        self.records.append(("info", message, extra))

    def debug(self, message: str, extra: dict | None = None, **_: object) -> None:
        self.records.append(("debug", message, extra))

    def error(self, message: str, extra: dict | None = None, **_: object) -> None:
        self.records.append(("error", message, extra))

    def critical(self, message: str, extra: dict | None = None, **_: object) -> None:
        self.records.append(("critical", message, extra))
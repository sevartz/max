from __future__ import annotations


class MaxApiError(Exception):
    def __init__(self, status: int, code: str | None = None, message: str | None = None) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"MAX API {status}: {code or ''} {message or ''}".strip())

    @property
    def retryable(self) -> bool:
        return self.status == 429 or self.status >= 500


class MaxNetworkError(MaxApiError):
    def __init__(self, message: str) -> None:
        super().__init__(0, "network", message)

    @property
    def retryable(self) -> bool:
        return True

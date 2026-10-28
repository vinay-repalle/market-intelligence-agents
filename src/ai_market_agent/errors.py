"""Exception hierarchy separating retryable from permanent failures."""

from __future__ import annotations


class AgentError(Exception):
    """Base class for all expected agent failures."""

    stage = "agent"


class ConfigError(AgentError):
    """Missing or invalid configuration. Never retried."""

    stage = "config"


class TransientError(AgentError):
    """Temporary failure (timeout, 5xx, rate limit). Safe to retry."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentError(AgentError):
    """Failure that retrying cannot fix (bad credentials, bad request)."""


class CollectionError(PermanentError):
    stage = "collection"


class GeminiError(PermanentError):
    stage = "gemini"


class GeminiAuthError(GeminiError):
    """Invalid/unauthorised API key or model access."""


class GeminiQuotaError(GeminiError):
    """Quota or rate limit exhausted and not recoverable within this run."""


class TelegramError(PermanentError):
    stage = "telegram"

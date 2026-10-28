"""Logging setup with secret redaction."""

from __future__ import annotations

import logging
import re
import sys

_TOKEN_IN_URL = re.compile(r"(bot)\d+:[A-Za-z0-9_-]+")
_KEY_PARAM = re.compile(r"(key=)[^&\s]+")


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***")
        text = _TOKEN_IN_URL.sub(r"\1***", text)
        return _KEY_PARAM.sub(r"\1***", text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = None
        return True


def setup_logging(secrets: list[str] | None = None) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter(secrets or []))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

"""Structured parser failure records for audit logs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParseFailure:
    code: str
    message: str
    recoverable: bool = True

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("parse failure code and message must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "recoverable": self.recoverable}


@dataclass(frozen=True)
class ParseAttempt:
    result: Any = None
    failure: ParseFailure | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and self.failure is None

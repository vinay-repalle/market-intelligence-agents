from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Story:
    title: str
    url: str
    source: str
    published: datetime | None
    snippet: str = ""
    source_weight: float = 1.0
    official: bool = False
    ai_only: bool = False
    also_covered_by: list[str] = field(default_factory=list)
    score: float = 0.0
    summary: str = ""

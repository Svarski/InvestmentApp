"""Alert data models for rule evaluation and notifications."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class RuleResult:
    """Pure rule evaluation result used by the alert engine."""

    key: str
    alert_type: str
    message: str
    severity: str
    level: Optional[float] = None


@dataclass(frozen=True)
class Alert:
    """Concrete alert message ready for delivery."""

    id: str
    type: str
    message: str
    severity: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

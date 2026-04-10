"""In-memory alert state tracking with dedupe and reset support."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class AlertState:
    """
    Keep in-memory state of triggered alert levels and rolling metrics.

    Example internal structure:
    {
      "VWCE_drawdown": {"triggered_levels": {-10.0, -20.0}},
      "vix_spike": {"triggered_levels": {25.0}}
    }
    """

    alerts: Dict[str, Dict[str, Set[float]]] = field(default_factory=dict)
    metrics: Dict[str, float] = field(default_factory=dict)

    def get_triggered_levels(self, key: str) -> Set[float]:
        """Return triggered levels set for an alert key."""
        return self.alerts.setdefault(key, {}).setdefault("triggered_levels", set())

    def is_level_triggered(self, key: str, level: float) -> bool:
        """Check whether a level was already triggered for key."""
        return level in self.get_triggered_levels(key)

    def mark_triggered(self, key: str, level: float) -> None:
        """Record a level as triggered for key."""
        self.get_triggered_levels(key).add(level)

    def reset_level(self, key: str, level: float) -> None:
        """Clear one triggered level for key."""
        self.get_triggered_levels(key).discard(level)

    def get_metric(self, name: str) -> Optional[float]:
        """Get a tracked numeric metric, if present."""
        return self.metrics.get(name)

    def set_metric(self, name: str, value: float) -> None:
        """Set a tracked numeric metric."""
        self.metrics[name] = value

    def to_dict(self) -> Dict[str, object]:
        """Serialize state into JSON-friendly dictionary."""
        return {
            "alerts": {
                key: {"triggered_levels": sorted(float(level) for level in data.get("triggered_levels", set()))}
                for key, data in self.alerts.items()
            },
            "metrics": {key: float(value) for key, value in self.metrics.items()},
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "AlertState":
        """Build AlertState from deserialized dictionary payload."""
        alerts_payload = payload.get("alerts", {}) if isinstance(payload, dict) else {}
        metrics_payload = payload.get("metrics", {}) if isinstance(payload, dict) else {}

        alerts: Dict[str, Dict[str, Set[float]]] = {}
        if isinstance(alerts_payload, dict):
            for key, value in alerts_payload.items():
                if not isinstance(key, str) or not isinstance(value, dict):
                    continue
                levels_raw = value.get("triggered_levels", [])
                levels_set: Set[float] = set()
                if isinstance(levels_raw, list):
                    for level in levels_raw:
                        try:
                            levels_set.add(float(level))
                        except (TypeError, ValueError):
                            continue
                alerts[key] = {"triggered_levels": levels_set}

        metrics: Dict[str, float] = {}
        if isinstance(metrics_payload, dict):
            for key, value in metrics_payload.items():
                if not isinstance(key, str):
                    continue
                try:
                    metrics[key] = float(value)
                except (TypeError, ValueError):
                    continue

        return cls(alerts=alerts, metrics=metrics)

    @classmethod
    def load_from_file(cls, file_path: str) -> "AlertState":
        """Load state from JSON file; return empty state on missing/corrupt file."""
        path = Path(file_path)
        if not path.exists():
            logger.info("Alert state file not found. Using empty state. path=%s", file_path)
            return cls()

        try:
            with path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            state = cls.from_dict(payload)
            logger.info("Alert state loaded from file. path=%s", file_path)
            return state
        except Exception as exc:
            logger.warning("Failed to load alert state file. Using empty state. path=%s error=%s", file_path, exc)
            return cls()

    def save_to_file(self, file_path: str) -> bool:
        """Persist state to JSON file. Returns True on success."""
        path = Path(file_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as file:
                json.dump(self.to_dict(), file, indent=2)
            logger.info("Alert state saved to file. path=%s", file_path)
            return True
        except Exception as exc:
            logger.warning("Failed to save alert state file. path=%s error=%s", file_path, exc)
            return False

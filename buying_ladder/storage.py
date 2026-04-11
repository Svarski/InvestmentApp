"""JSON persistence for buying ladder settings (aligned with other app JSON state files)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from buying_ladder.models import BuyingLadderSettings, default_buying_ladder_settings

logger = logging.getLogger("buying_ladder.storage")

DEFAULT_SETTINGS_PATH = "./data/buying_ladder_settings.json"


def settings_file_path() -> Path:
    return Path(os.getenv("BUYING_LADDER_SETTINGS_FILE", DEFAULT_SETTINGS_PATH)).expanduser()


def load_buying_ladder_settings() -> BuyingLadderSettings:
    path = settings_file_path()
    if not path.exists():
        logger.info("Buying ladder settings file not found; using defaults. path=%s", path)
        return default_buying_ladder_settings()
    try:
        with path.open("r", encoding="utf-8") as file:
            payload: Dict[str, Any] = json.load(file)
        settings = BuyingLadderSettings.from_dict(payload if isinstance(payload, dict) else {})
        logger.info("Buying ladder settings loaded. path=%s", path)
        return settings
    except Exception as exc:
        logger.warning("Failed to load buying ladder settings; using defaults. path=%s error=%s", path, exc)
        return default_buying_ladder_settings()


def save_buying_ladder_settings(settings: BuyingLadderSettings) -> bool:
    path = settings_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(settings.to_dict(), file, indent=2)
        logger.info("Buying ladder settings saved. path=%s", path)
        return True
    except Exception as exc:
        logger.warning("Failed to save buying ladder settings. path=%s error=%s", path, exc)
        return False

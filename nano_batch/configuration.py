"""Shared, browser-free configuration parsing for CLI and GUI Preview."""

from __future__ import annotations

import json
from pathlib import Path

from .models import GenerationSettings


def _load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Configuration root must be a JSON object.")
    return raw


def _generation_settings(
    config: dict,
    timeout_override: int | None = None,
    *,
    variants_override: int | None = None,
) -> GenerationSettings:
    raw = config.get("generation") or {}
    if not isinstance(raw, dict):
        raise ValueError("generation configuration must be a JSON object.")
    settings = GenerationSettings(
        mode=str(raw.get("mode", "image")),
        aspect_ratio=str(raw.get("aspect_ratio", "16:9")),
        variants=int(
            variants_override
            if variants_override is not None
            else raw.get("variants", 2)
        ),
        preferred_model_hint=raw.get("preferred_model_hint"),
        result_timeout_seconds=int(
            timeout_override
            if timeout_override is not None
            else raw.get("result_timeout_seconds", 300)
        ),
    )
    settings.validate()
    return settings

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import Batch, BatchItem


SINGLE_PROMPT_VISUAL_ID = "prompt_0001_visual_001"
PROMPT_WRAPPER_VARIANTS = 1


def _semantic_batch_id(items: list[BatchItem]) -> str:
    payload = []
    for item in items:
        semantic_item: dict[str, Any] = {
            "order": item.order,
            "visual_id": item.visual_id,
            "prompt": item.prompt,
        }
        optional_generation_fields = {
            "mode": item.mode,
            "aspect_ratio": item.aspect_ratio,
            "preferred_model_hint": item.preferred_model_hint,
            "variants": item.variants,
        }
        semantic_item.update(
            {
                key: value
                for key, value in optional_generation_fields.items()
                if value is not None
            }
        )
        payload.append(semantic_item)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _pick_prompt(raw: dict[str, Any]) -> str:
    value = raw.get("generation_prompt")
    if value is None:
        value = raw.get("prompt")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Every item must contain a non-empty generation_prompt or prompt.")
    return value.strip()


def _optional_nonempty_string(raw: dict[str, Any], field: str, item_number: int) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Item #{item_number} has invalid {field}.")
    return value.strip()


def _optional_positive_integer(raw: dict[str, Any], field: str, item_number: int) -> int | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"Item #{item_number} has invalid positive integer {field}.")
    return value


def _prompt_wrapper_batch(prompts: list[str], *, source_schema: str) -> Batch:
    items: list[BatchItem] = []
    for order, prompt in enumerate(prompts, start=1):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Every prompt must be a non-empty string.")
        items.append(
            BatchItem(
                order=order,
                visual_id=f"prompt_{order:04d}_visual_001",
                prompt=prompt.strip(),
                mode="image",
                aspect_ratio="16:9",
                # Retained only to preserve established prompt-wrapper Batch IDs;
                # production no longer enforces this historical hint.
                preferred_model_hint="Nano Banana 2",
                variants=PROMPT_WRAPPER_VARIANTS,
            )
        )
    if not items:
        raise ValueError("At least one prompt is required.")
    return Batch(
        batch_id=_semantic_batch_id(items),
        source_schema=source_schema,
        items=tuple(items),
    )


def single_prompt_batch(prompt: str) -> Batch:
    """Build a one-order, one-variant in-memory prompt-wrapper batch."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Prompt must be a non-empty string.")
    return _prompt_wrapper_batch(
        [prompt],
        source_schema="nano-banana-single-prompt",
    )


def load_prompts_file(path: Path) -> Batch:
    """Load ordered UTF-8 prompt lines into the existing validated batch model."""
    if not path.exists():
        raise FileNotFoundError(path)
    prompts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        prompt = line.strip()
        if not prompt or prompt.startswith("#"):
            continue
        prompts.append(prompt)
    if not prompts:
        raise ValueError(
            "Prompts file contains no prompts after blank and comment lines are ignored."
        )
    return _prompt_wrapper_batch(
        prompts,
        source_schema="nano-banana-prompts-file",
    )


def load_batch(path: Path) -> Batch:
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Batch JSON root must be an object.")
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Batch must contain a non-empty items array.")

    items: list[BatchItem] = []
    seen_orders: set[int] = set()
    seen_visual_ids: set[str] = set()
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Item #{index} must be an object.")
        order = raw.get("order")
        visual_id = raw.get("visual_id")
        if not isinstance(order, int) or order <= 0:
            raise ValueError(f"Item #{index} has invalid positive integer order.")
        if not isinstance(visual_id, str) or not visual_id.strip():
            raise ValueError(f"Item #{index} has invalid visual_id.")
        visual_id = visual_id.strip()
        prompt = _pick_prompt(raw)
        if order in seen_orders:
            raise ValueError(f"Duplicate order: {order}")
        if visual_id in seen_visual_ids:
            raise ValueError(f"Duplicate visual_id: {visual_id}")
        seen_orders.add(order)
        seen_visual_ids.add(visual_id)
        items.append(
            BatchItem(
                order=order,
                visual_id=visual_id,
                prompt=prompt,
                mode=_optional_nonempty_string(raw, "mode", index),
                aspect_ratio=_optional_nonempty_string(raw, "aspect_ratio", index),
                preferred_model_hint=_optional_nonempty_string(
                    raw, "preferred_model_hint", index
                ),
                variants=_optional_positive_integer(raw, "variants", index),
            )
        )

    items.sort(key=lambda item: item.order)
    expected = list(range(1, len(items) + 1))
    actual = [item.order for item in items]
    if actual != expected:
        raise ValueError(f"Orders must be contiguous starting at 1. Expected {expected}, got {actual}.")

    return Batch(
        batch_id=_semantic_batch_id(items),
        source_schema=str(data.get("schema") or "unknown"),
        items=tuple(items),
        source_batch_revision=data.get("batch_revision"),
        source_visual_manifest_revision=data.get("source_visual_manifest_revision"),
    )

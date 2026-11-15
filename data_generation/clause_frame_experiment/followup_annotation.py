"""One model annotation contract for every item in the unified unknown queue."""
from __future__ import annotations

import json
from typing import Any

from .gap_selection import TYPE_ORDER
from .followup_annotation_prompts import UNKNOWN_ANNOTATION_PROMPT


def validate_unknown_annotations(items: list[dict], raw: Any) -> dict[str, dict]:
    ids = {item["followup_id"] for item in items}
    if not isinstance(raw, dict) or set(raw) != {"annotations"} or not isinstance(raw["annotations"], list):
        raise ValueError("expected annotations list")
    annotations = {}
    for row in raw["annotations"]:
        if not isinstance(row, dict) or set(row) != {"followup_id", "type", "reason"}:
            raise ValueError("invalid annotation fields")
        key = row["followup_id"]
        if not isinstance(key, str) or key not in ids or key in annotations:
            raise ValueError("unknown or duplicate followup_id")
        if row["type"] not in TYPE_ORDER:
            raise ValueError("invalid unknown type")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError("annotation reason required")
        annotations[key] = dict(row)
    if set(annotations) != ids:
        raise ValueError("annotations must cover every unknown exactly once")
    return annotations


def annotate_unknowns_with_llm(client, frame, items: list[dict], model: str,
                               *, schema_context=None) -> tuple[dict, dict]:
    if not items:
        return {"annotations": []}, {"calls": []}
    context = dict(schema_context or {})
    public = {key: context.get(key, {} if key == "column_language" else [])
              for key in ("tables", "foreign_keys", "column_language")}
    payload = {"original_text": frame.original_text, "unknown_items": items,
               "schema_context": public}
    messages = [{"role": "system", "content": UNKNOWN_ANNOTATION_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    audit = {"stage": "unified_unknown_annotation", "model": model,
             "messages": messages, "temperature": 0}
    try:
        response = client.chat.completions.create(model=model, temperature=0, messages=messages,
                                                   response_format={"type": "json_object"})
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        audit.update(content=content, response_id=getattr(response, "id", None),
                     usage=usage.model_dump() if usage and hasattr(usage, "model_dump") else None)
        raw = json.loads(content)
        validate_unknown_annotations(items, raw)
        return raw, {"calls": [audit]}
    except Exception as exc:
        exc.audit_calls = [audit]
        raise

"""LLM adapter for the minimal clause frame contract."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from openai import OpenAI

from .model import QueryFrame, apply_gaps, parse_query_frame
from .prompts import FRAME_SYSTEM_PROMPT, GAP_SYSTEM_PROMPT


def _gap_schema_view(schema_context: Mapping[str, Any]) -> dict[str, Any]:
    """Keep schema identifiers and one authoritative business description per column."""
    tables = []
    for table in schema_context.get("tables", []):
        tables.append({
            "name": table.get("name"),
            "columns": [column.get("name") for column in table.get("columns", [])],
        })
    descriptions = {}
    for key, value in schema_context.get("column_language", {}).items():
        if isinstance(value, Mapping):
            description = value.get("exact") or value.get("paraphrase")
        else:
            description = value
        if description:
            descriptions[key] = description
    foreign_keys = schema_context.get("foreign_keys", [])
    return {
        "tables": tables,
        "column_meanings": descriptions,
        "foreign_keys": foreign_keys,
        "relationship_paths": _two_hop_paths(foreign_keys),
    }


def _two_hop_paths(foreign_keys: list[dict[str, Any]]) -> list[str]:
    """Expose two-edge paths implied by declared foreign keys, including alternatives."""
    edges: list[tuple[str, str, str, str]] = []
    for fk in foreign_keys:
        source = fk.get("from_table")
        target = fk.get("to_table")
        if source and target:
            edges.append((source, fk.get("from_column", "?"),
                          target, fk.get("to_column", "?")))
            edges.append((target, fk.get("to_column", "?"),
                          source, fk.get("from_column", "?")))

    adjacent = {(source, target) for source, _, target, _ in edges}
    paths: dict[tuple[str, str], set[tuple[str, str, str, str, str]]] = {}
    for source, source_column, middle, middle_column in edges:
        for next_source, next_column, target, target_column in edges:
            if next_source != middle or target in {source, middle}:
                continue
            if (source, target) in adjacent or (target, source) in adjacent:
                continue
            paths.setdefault((source, target), set()).add(
                (middle, source_column, middle_column, next_column, target_column))

    rendered = []
    for (source, target), candidates in sorted(paths.items()):
        for middle, source_column, middle_column, next_column, target_column in sorted(candidates):
            rendered.append(f"{source} -> {middle} -> {target} "
                            f"({source}.{source_column} = {middle}.{middle_column}; "
                            f"{middle}.{next_column} = {target}.{target_column})")
    return rendered


class FrameParserError(ValueError):
    def __init__(self, message: str, *, raw_content: str):
        super().__init__(message)
        self.raw_content = raw_content


def build_frame_prompt(request_text: str, *, schema_context: Mapping[str, Any] | None = None,
                       db_name: str | None = None) -> str:
    context = dict(schema_context or {})
    tables = context.get("tables", [])
    foreign_keys = context.get("foreign_keys", [])
    data_description = context.get("column_language", {})
    return FRAME_SYSTEM_PROMPT.replace(
        "[[DB_name]]", db_name or "unspecified",
    ).replace(
        "[[db_schema]]", json.dumps({"tables": tables, "foreign_keys": foreign_keys}, ensure_ascii=False, indent=2),
    ).replace(
        "[[data_description]]", json.dumps(data_description, ensure_ascii=False, indent=2),
    ).replace("[[user_request]]", request_text)


def build_gap_context_prompt(*, db_name: str | None, schema_context: Mapping[str, Any],
                             frame: QueryFrame) -> str:
    """Render the public schema for gap detection in the same readable sections
    used by the segmentation prompt. Hidden evaluation fields are never included.
    """
    context = dict(schema_context or {})
    return "\n".join([
        f'Database: "{db_name or "unspecified"}"',
        "# DB Schema Info:",
        json.dumps({"tables": context.get("tables", [])}, ensure_ascii=False, indent=2),
        "# Column Meanings:",
        json.dumps(context.get("column_language", {}), ensure_ascii=False, indent=2),
        "# Foreign Keys:",
        json.dumps(context.get("foreign_keys", []), ensure_ascii=False, indent=2),
        "# Parsed Request:",
        json.dumps(frame.to_json(), ensure_ascii=False, indent=2),
    ])


def build_gap_system_prompt(*, db_name: str | None,
                            schema_context: Mapping[str, Any]) -> str:
    """Render the gap prompt with the same schema header as stage one."""
    context = dict(schema_context or {})
    schema_view = _gap_schema_view(context)
    return GAP_SYSTEM_PROMPT.replace(
        "[[DB_name]]", db_name or "unspecified",
    ).replace(
        "[[db_schema]]", json.dumps({
            "tables": schema_view["tables"],
            "foreign_keys": schema_view["foreign_keys"],
        }, ensure_ascii=False, indent=2),
    ).replace(
        "[[data_description]]", json.dumps(schema_view["column_meanings"],
                                            ensure_ascii=False, indent=2),
    ).replace(
        "[[frame]]", json.dumps(context.get("frame", {}), ensure_ascii=False, indent=2),
    ).replace(
        "# Parsed Request:",
        "# Relationship Paths (derived from declared foreign keys):\n"
        + json.dumps(schema_view["relationship_paths"], ensure_ascii=False, indent=2)
        + "\n# Parsed Request:",
    )


def parse_query_frame_with_llm(
    client: OpenAI,
    request_text: str,
    model: str,
    *,
    schema_context: Mapping[str, Any] | None = None,
    db_name: str | None = None,
    cached_segmentation: dict | None = None,
    on_segmented: Callable[[QueryFrame], None] | None = None,
) -> tuple[QueryFrame, dict[str, Any]]:
    if not isinstance(request_text, str) or not request_text.strip():
        raise ValueError("request_text must be non-empty")
    prompt = build_frame_prompt(request_text, schema_context=schema_context, db_name=db_name)
    calls = []
    def call(stage, messages):
        record = {"stage": stage, "messages": messages, "model": model, "temperature": 0}
        calls.append(record)
        try:
            response = client.chat.completions.create(
                model=model, temperature=0, messages=messages,
                response_format={"type": "json_object"})
            content = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            record.update(content=content, response_id=response.id,
                          usage=usage.model_dump() if usage else None)
            return json.loads(content)
        except Exception as exc:
            record["error"] = str(exc)
            raise
    try:
        if cached_segmentation is None:
            raw = call("segmentation", [{"role": "system", "content": prompt}])
        else:
            expected_messages = [{"role": "system", "content": prompt}]
            if cached_segmentation.get("messages") != expected_messages or cached_segmentation.get("model") != model:
                raise ValueError("cached segmentation does not match prompt/model")
            calls.append({**cached_segmentation, "reused": True})
            raw = json.loads(cached_segmentation["content"])
        frame = parse_query_frame(raw)
        if frame.original_text != request_text:
            raise ValueError("original_text does not exactly match input")
        if on_segmented is not None:
            on_segmented(frame)
        context = dict(schema_context or {})
        public = {key: context.get(key, {} if key == "column_language" else [])
                  for key in ("tables", "foreign_keys", "column_language")}
        gaps = call("gap_detection", [{"role": "system", "content": build_gap_system_prompt(
            db_name=db_name, schema_context={**public, "frame": frame.to_json()})}])
        final = apply_gaps(frame, gaps)
        return final, {"model": model, "calls": calls}
    except Exception as exc:
        calls.extend(getattr(exc, "audit_calls", []))
        if not isinstance(exc, (ValueError, TypeError)):
            exc.audit_calls = calls
            raise
        error = FrameParserError(str(exc), raw_content=calls[-1].get("content", "") if calls else "")
        error.audit_calls = calls
        raise error from exc

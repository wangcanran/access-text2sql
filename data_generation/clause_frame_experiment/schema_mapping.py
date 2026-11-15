"""Map only non-gap request fragments, then check them against explicit policy."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .model import QueryFrame


def clear_fragments(frame: QueryFrame) -> list[dict[str, str]]:
    """Return whole first-stage items that contain no gap.

    We do not subtract gap substrings from an item.  A residual substring can be
    grammatical glue rather than an independently mappable business request.
    The first-stage parser must create atomic items before such mixed content can
    be mapped safely.
    """
    result = []
    for item in frame.outputs + frame.modifiers:
        if any(gap["item_id"] == item["item_id"] for gap in frame.gaps):
            continue
        result.append({"item_id": item["item_id"], "fragment_id": f"{item['item_id']}:clear:1",
                       "span": item["span"]})
    return result


def _column_index(context: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    columns: dict[str, str] = {}
    folded: dict[str, str] = {}
    for table in context.get("tables", []):
        if not isinstance(table, Mapping) or not isinstance(table.get("name"), str):
            raise ValueError("invalid schema table")
        for column in table.get("columns", []):
            if not isinstance(column, Mapping) or not isinstance(column.get("name"), str):
                raise ValueError("invalid schema column")
            qualified = f"{table['name']}.{column['name']}"
            if qualified in columns:
                raise ValueError(f"duplicate schema column: {qualified}")
            folded_name = qualified.casefold()
            if folded_name in folded:
                raise ValueError(f"case-insensitive schema column collision: {qualified}")
            columns[qualified] = column.get("phrase") or ""
            folded[folded_name] = qualified
    return columns, folded


def _column_language_index(context: Mapping[str, Any], folded_columns: Mapping[str, str]) -> dict[str, Any]:
    language = context.get("column_language", {})
    if not isinstance(language, Mapping):
        return {}
    result: dict[str, Any] = {}
    for name, meaning in language.items():
        if not isinstance(name, str):
            continue
        canonical = folded_columns.get(name.casefold())
        if canonical is not None:
            if canonical in result:
                raise ValueError(f"duplicate case-insensitive column_language entry: {canonical}")
            result[canonical] = meaning
    return result


def _mapping_schema_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Expose one canonical physical name per column with meanings attached."""
    _, folded = _column_index(context)
    language = _column_language_index(context, folded)
    tables = []
    for table in context.get("tables", []):
        table_columns = []
        for column in table.get("columns", []):
            qualified = f"{table['name']}.{column['name']}"
            entry = {"name": column["name"]}
            if column.get("phrase"):
                entry["schema_meaning"] = column["phrase"]
            meaning = language.get(qualified)
            if isinstance(meaning, Mapping):
                entry["column_language"] = dict(meaning)
            elif meaning:
                entry["column_language"] = meaning
            table_columns.append(entry)
        tables.append({"name": table["name"], "columns": table_columns})
    return {"tables": tables, "foreign_keys": context.get("foreign_keys", [])}


def validate_mapping_for_context(frame: QueryFrame, raw: Mapping[str, Any], context: Mapping[str, Any],
                                 allowed_columns) -> dict[str, list[dict[str, Any]]]:
    fragments = {item["fragment_id"]: item for item in clear_fragments(frame)}
    if not isinstance(raw, Mapping) or set(raw) != {"mappings"} or not isinstance(raw["mappings"], list):
        raise ValueError("expected mappings list")
    schema_columns, folded_columns = _column_index(context)
    allowed = None if allowed_columns is None else set(allowed_columns)
    if allowed is not None and (not all(isinstance(x, str) for x in allowed) or not allowed <= schema_columns.keys()):
        raise ValueError("policy contains invalid schema columns")

    seen: set[str] = set()
    result = []
    for mapping in raw["mappings"]:
        if not isinstance(mapping, Mapping) or set(mapping) != {"fragment_id", "status", "columns"}:
            raise ValueError("mapping must contain fragment_id, status, columns")
        fragment_id = mapping["fragment_id"]
        if not isinstance(fragment_id, str) or fragment_id not in fragments or fragment_id in seen:
            raise ValueError("mapping must cover each clear item exactly once")
        status, columns = mapping["status"], mapping["columns"]
        if status not in {"mapped", "unresolved"} or not isinstance(columns, list) or any(not isinstance(c, str) for c in columns):
            raise ValueError("invalid mapping values")
        if status == "mapped" and not columns:
            raise ValueError("mapped status requires at least one column")
        if status == "unresolved" and columns:
            raise ValueError("unresolved status must not contain columns")
        canonical_columns = []
        unknown = []
        for column in columns:
            canonical = folded_columns.get(column.casefold())
            if canonical is None:
                unknown.append(column)
            elif canonical not in canonical_columns:
                canonical_columns.append(canonical)
        denied = [] if allowed is None else [column for column in canonical_columns if column not in allowed]
        accessible = list(canonical_columns) if allowed is None else [column for column in canonical_columns if column in allowed]
        if unknown:
            runtime_status = "mapping_invalid"
        elif status == "unresolved":
            runtime_status = "mapping_unresolved"
        elif allowed is None:
            runtime_status = "permission_context_missing"
        elif not accessible:
            runtime_status = "clear_but_denied"
        else:
            runtime_status = "clear_and_allowed"
        fragment = fragments[fragment_id]
        result.append({"item_id": fragment["item_id"], "fragment_id": fragment_id,
                       "span": fragment["span"], "columns": accessible,
                       "requested_columns": list(columns),
                       "status": runtime_status, "unknown_columns": unknown, "denied_columns": denied})
        seen.add(fragment_id)
    if seen != set(fragments):
        raise ValueError("mappings must cover all wholly non-gap items")
    return {"mappings": result}


MAPPING_PROMPT = r'''Map each supplied clear request fragment to the database schema.
Use the full original request, public schema, foreign keys and column descriptions.
The supplied fragments are the complete set of clear items to map.

Return one JSON object in this exact shape:
{"mappings":[{"fragment_id":"o1:clear:1","status":"mapped","columns":["table.column"]}]}

For every supplied fragment, return one mapping entry:
- fragment_id: copy the supplied identifier exactly;
- status: use "mapped" when the meaning has one or more defensible schema columns,
  otherwise use "unresolved";
- columns: qualified schema column names that express the fragment. Copy each
  name exactly as written in the supplied schema; descriptions explain meaning
  but are not alternate identifiers.
  Include all columns jointly needed for a requested calculation. When wording
  genuinely matches equivalent columns in multiple tables, include those matches.
  For an unresolved fragment, use an empty list.

Example with a supported mapping:
Fragment: {"fragment_id":"o1:clear:1","span":"customer name"}
Schema meaning: customer.name is the customer's name.
Output: {"mappings":[{"fragment_id":"o1:clear:1","status":"mapped","columns":["customer.name"]}]}

Example without a supported mapping:
Fragment: {"fragment_id":"o2:clear:1","span":"the relevant details"}
Schema: no column meaning supports this phrase.
Output: {"mappings":[{"fragment_id":"o2:clear:1","status":"unresolved","columns":[]}]}

The response entries follow the supplied fragment order. The runner checks that the
qualified columns exist and applies the current role's policy after mapping.
'''


def map_clear_with_llm(client, frame: QueryFrame, context: Mapping[str, Any], model: str):
    fragments = clear_fragments(frame)
    if not fragments:
        return {"mappings": []}, {"calls": []}
    public_context = _mapping_schema_context(context)
    messages = [{"role": "system", "content": MAPPING_PROMPT},
                {"role": "user", "content": json.dumps({"original_text": frame.original_text,
                 "fragments": fragments, "schema_context": public_context}, ensure_ascii=False)}]
    record = {"stage": "schema_mapping", "model": model, "messages": messages, "temperature": 0}
    try:
        response = client.chat.completions.create(model=model, temperature=0, messages=messages,
                                                   response_format={"type": "json_object"})
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        record.update(content=content, response_id=getattr(response, "id", None),
                      usage=usage.model_dump() if usage and hasattr(usage, "model_dump") else None)
        return json.loads(content), {"calls": [record]}
    except Exception as exc:
        exc.audit_calls = [record]
        raise


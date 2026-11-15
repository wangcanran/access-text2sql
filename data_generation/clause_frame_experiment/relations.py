"""Independent linguistic links between first-stage request fragments."""

import json
from typing import Any, Mapping

from .model import QueryFrame
from .prompts import RELATION_SYSTEM_PROMPT


def validate_relations(frame: QueryFrame, raw: Mapping[str, Any]) -> dict[str, list[dict[str, str]]]:
    if not isinstance(raw, Mapping) or set(raw) != {"edges"} or not isinstance(raw["edges"], list):
        raise ValueError("expected edges list only")
    ids = {item["item_id"] for item in frame.outputs + frame.modifiers}
    seen = set()
    edges = []
    for edge in raw["edges"]:
        if not isinstance(edge, dict) or set(edge) != {"source_id", "target_id"}:
            raise ValueError("edge must contain source_id and target_id only")
        pair = (edge["source_id"], edge["target_id"])
        if not all(isinstance(value, str) for value in pair):
            raise ValueError("relation endpoints must be item IDs")
        if pair[0] not in ids or pair[1] not in ids or pair[0] == pair[1] or pair in seen:
            raise ValueError("invalid relation endpoints or duplicate edge")
        seen.add(pair)
        edges.append(dict(edge))
    adjacency = {node: [] for node in ids}
    for source, target in seen:
        adjacency[source].append(target)
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise ValueError("relation graph contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for target in adjacency[node]:
            visit(target)
        visiting.remove(node)
        visited.add(node)
    for node in ids:
        visit(node)
    return {"edges": edges}


def annotate_relations_with_llm(client, frame: QueryFrame, model: str):
    """Annotate links without mutating the frame or passing gap/schema/policy data."""
    payload = {"original_text": frame.original_text,
               "outputs": [dict(item) for item in frame.outputs],
               "modifiers": [dict(item) for item in frame.modifiers]}
    prompt = RELATION_SYSTEM_PROMPT.replace(
        "[[frame]]", json.dumps(payload, ensure_ascii=False, indent=2))
    messages = [{"role": "system", "content": prompt}]
    record = {"stage": "relation_annotation", "model": model,
              "messages": messages, "temperature": 0}
    try:
        response = client.chat.completions.create(
            model=model, temperature=0, messages=messages,
            response_format={"type": "json_object"})
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        record.update(content=content, response_id=getattr(response, "id", None),
                      usage=usage.model_dump() if usage and hasattr(usage, "model_dump") else None)
        result = validate_relations(frame, json.loads(content))
        return result, {"calls": [record]}
    except Exception as exc:
        record["error"] = str(exc)
        exc.audit_calls = [record]
        raise

"""Verbatim request fragments; no inferred query plan."""
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class QueryFrame:
    original_text: str
    outputs: tuple[dict, ...]
    modifiers: tuple[dict, ...]
    gaps: tuple[dict, ...] = ()

    def to_json(self):
        return {"original_text": self.original_text,
                "outputs": [dict(x) for x in self.outputs],
                "modifiers": [dict(x) for x in self.modifiers],
                "gaps": [dict(x) for x in self.gaps]}


def parse_query_frame(raw: Mapping[str, Any], *, with_gaps=False) -> QueryFrame:
    expected = {"original_text", "outputs", "modifiers"} | ({"gaps"} if with_gaps else set())
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise ValueError("expected original_text, outputs and modifiers only")
    original = raw["original_text"]
    if not isinstance(original, str) or not original.strip():
        raise ValueError("original_text must be nonempty")
    seen = set()
    parsed = {}
    for bucket in ("outputs", "modifiers"):
        if not isinstance(raw[bucket], list):
            raise ValueError(f"{bucket} must be a list")
        items = []
        for value in raw[bucket]:
            fields = {"item_id", "span"}
            if not isinstance(value, dict) or set(value) != fields:
                raise ValueError(f"invalid fields in {bucket}")
            for key in fields:
                if not isinstance(value[key], str) or not value[key].strip():
                    raise ValueError(f"invalid {key}")
            if value["item_id"] in seen:
                raise ValueError("duplicate item ID")
            seen.add(value["item_id"])
            if value["span"] not in original:
                raise ValueError("span is not an exact substring")
            items.append(dict(value))
        parsed[bucket] = tuple(items)
    frame = QueryFrame(original, **parsed)
    return apply_gaps(frame, {"gaps": raw["gaps"]}) if with_gaps else frame


def apply_gaps(frame: QueryFrame, raw: dict) -> QueryFrame:
    if not isinstance(raw, dict) or set(raw) != {"gaps"} or not isinstance(raw["gaps"], list):
        raise ValueError("expected gaps list only")
    gaps = []
    seen = set()
    items = {x["item_id"]: x for x in frame.outputs + frame.modifiers}
    for gap in raw["gaps"]:
        if not isinstance(gap, dict) or set(gap) != {"item_id", "span", "missing"}:
            raise ValueError("expected item_id, span and missing in each gap")
        if any(not isinstance(v, str) or not v.strip() for v in gap.values()):
            raise ValueError("gap fields must be nonempty strings")
        item_id, span = gap["item_id"], gap["span"]
        if item_id not in items:
            raise ValueError("unknown gap item_id")
        if span not in items[item_id]["span"]:
            raise ValueError("gap span must be an exact substring of its parent fragment")
        signature = (item_id, span, gap["missing"])
        if signature in seen:
            raise ValueError("duplicate gap")
        seen.add(signature)
        gaps.append(dict(gap))
    # Neither an empty gap list nor a valid contract proves semantic completeness.
    return QueryFrame(frame.original_text, frame.outputs, frame.modifiers, tuple(gaps))

"""Pure deterministic selection of already annotated gaps; no LLM or prompt imports.

No permission checks, candidate generation, answer updates or completion decisions.
"""
from __future__ import annotations
import hashlib
import json
from .model import QueryFrame

TYPE_ORDER = ("subject", "output", "calculation", "condition", "scope", "other")


def gap_catalog(frame: QueryFrame) -> list[dict]:
    # Content identity survives array reordering, but not a changed original request
    # or rewritten gap. Such edits require fresh annotation, not stale resolved IDs.
    result = []
    for gap in frame.gaps:
        identity = json.dumps([frame.original_text, gap["item_id"], gap["span"], gap["missing"]],
                              ensure_ascii=False, separators=(",", ":"))
        key = "g_" + hashlib.sha256(identity.encode()).hexdigest()
        result.append({"gap_id": key, **gap})
    if len({g["gap_id"] for g in result}) != len(result):
        raise ValueError("duplicate gap identity")
    return result


def validate_annotations(frame: QueryFrame, raw: dict) -> dict[str, dict]:
    ids = {g["gap_id"] for g in gap_catalog(frame)}
    if not isinstance(raw, dict) or set(raw) != {"annotations"} or not isinstance(raw["annotations"], list):
        raise ValueError("expected annotations list")
    records = {}
    for row in raw["annotations"]:
        if not isinstance(row, dict) or set(row) != {"gap_id", "type", "reason"}:
            raise ValueError("invalid annotation fields")
        key = row["gap_id"]
        if not isinstance(key, str) or key not in ids or key in records:
            raise ValueError("unknown or duplicate gap_id")
        if row["type"] not in TYPE_ORDER:
            raise ValueError("invalid gap type")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError("reason required")
        records[key] = dict(row)
    if set(records) != ids:
        raise ValueError("annotations must cover exactly the supplied gaps")
    return records


def select_next_gap(frame: QueryFrame, annotations: dict, *, resolved_ids=(), deferred_ids=()) -> dict:
    records = validate_annotations(frame, annotations)
    catalog = {g["gap_id"]: g for g in gap_catalog(frame)}
    resolved, deferred = set(resolved_ids), set(deferred_ids)
    if not (resolved | deferred) <= set(catalog) or resolved & deferred:
        raise ValueError("invalid resolved/deferred IDs")
    pending = set(catalog) - resolved
    eligible = pending - deferred
    ranking = [{"gap_id": key, "type": records[key]["type"],
                "type_rank": TYPE_ORDER.index(records[key]["type"]),
                "reason": records[key]["reason"]} for key in eligible]
    ranking.sort(key=lambda r: (r["type_rank"],
                               frame.original_text.find(catalog[r["gap_id"]]["span"]), r["gap_id"]))
    return {"status": "selected" if ranking else ("no_pending_gaps" if not pending else "no_eligible_gap"),
            "selected_gap": catalog[ranking[0]["gap_id"]] if ranking else None,
            "ranking": ranking,
            "deferred_ids": sorted(deferred), "resolved_ids": sorted(resolved)}


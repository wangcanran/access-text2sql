"""Unify gap and clear-but-denied items for deterministic follow-up ordering."""
from .gap_selection import TYPE_ORDER
from .known_state import initialize_known_state
from .followup_annotation import validate_unknown_annotations


def prepare_interaction_state(frame, mappings, gap_annotations):
    """Build the pre-candidate known state and one unified unknown queue."""
    state = initialize_known_state(frame, mappings)
    items = build_followup_items(frame, mappings, gap_annotations)
    unresolved = [dict(row) for row in mappings.get("mappings", [])
                  if row.get("status") == "mapping_unresolved"]
    return {"state": state, "followups": items, "unresolved_mappings": unresolved}

def build_followup_items(frame, mappings, gap_annotations=None):
    items=[]
    for row in mappings.get("mappings",[]):
        if row["status"] in {"clear_but_denied", "mapping_invalid",
                              "permission_context_missing"}:
            item = next(x for x in frame.outputs + frame.modifiers
                        if x["item_id"] == row["item_id"])
            items.append({"followup_id":"mapping:"+row["item_id"],"source":"unknown",
                          "item_id":row["item_id"],"span":item["span"],
                          "source_position":frame.original_text.find(item["span"]),
                          "reason":("clear requirement is not accessible" if row["status"] == "clear_but_denied"
                                     else row["status"]),"columns":row.get("columns", [])})
    for gap in frame.gaps:
        gid=next(x["gap_id"] for x in __import__('clause_frame_experiment.gap_selection',fromlist=['gap_catalog']).gap_catalog(frame) if x["item_id"]==gap["item_id"] and x["span"]==gap["span"] and x["missing"]==gap["missing"])
        items.append({"followup_id":"gap:"+gid,"source":"unknown","gap_id":gid,
                      "item_id":gap["item_id"],"span":gap["span"],"missing":gap["missing"],
                      "source_position":frame.original_text.find(gap["span"])})
    return items

def select_next_followup(items, *, done=()):
    done=set(done); pending=[x for x in items if x["followup_id"] not in done]
    pending.sort(key=lambda x:(TYPE_ORDER.index(x["type"]),x.get("span",""),x["followup_id"]))
    return {"status":"selected" if pending else "no_pending_followups","selected":pending[0] if pending else None,"ranking":pending}


def select_next_unknown(items, annotations, *, resolved_ids=()):
    """Select from all unresolved items using their unified type annotations."""
    records = validate_unknown_annotations(items, annotations)
    resolved = set(resolved_ids)
    ids = {item["followup_id"] for item in items}
    if not resolved <= ids:
        raise ValueError("unknown resolved followup_id")
    pending = []
    for item in items:
        if item["followup_id"] in resolved:
            continue
        annotation = records[item["followup_id"]]
        pending.append({**item, "type": annotation["type"],
                        "annotation_reason": annotation["reason"]})
    pending.sort(key=lambda x:(TYPE_ORDER.index(x["type"]),
                               x.get("source_position", 10**9),
                               x.get("span", ""), x["followup_id"]))
    return {"status":"selected" if pending else "no_pending_unknowns",
            "selected":pending[0] if pending else None,"ranking":pending}

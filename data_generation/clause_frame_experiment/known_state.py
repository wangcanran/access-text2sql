from dataclasses import dataclass
from .gap_selection import gap_catalog

@dataclass(frozen=True)
class KnownState:
    known: tuple[dict, ...] = ()
    unknown: tuple[dict, ...] = ()
    resolved_gaps: tuple[str, ...] = ()
    def to_json(self):
        return {"known": list(self.known), "unknown": list(self.unknown), "resolved_gaps": list(self.resolved_gaps)}

def add_mapping(state, mapping):
    if mapping["status"] != "clear_and_allowed": raise ValueError("mapping is not allowed")
    if any(x.get("item_id") == mapping["item_id"] for x in state.known): return state
    item = {"item_id": mapping["item_id"], "columns": tuple(mapping["columns"]), "source": "clear_mapping"}
    return KnownState(state.known + (item,), state.unknown, state.resolved_gaps)

def add_denied(state, mapping):
    if mapping["status"] != "clear_but_denied": raise ValueError("mapping is not denied")
    return add_unknown_mapping(state, mapping, reason="clear requirement is not accessible")


def add_unknown_mapping(state, mapping, *, reason=None):
    """Put a non-gap mapping that cannot enter known into the unified unknown queue."""
    if mapping.get("status") not in {
        "clear_but_denied", "mapping_invalid", "permission_context_missing",
    }:
        raise ValueError("mapping is not an unresolved or inaccessible item")
    if any(x.get("item_id") == mapping["item_id"] for x in state.unknown):
        return state
    item = {"item_id": mapping["item_id"],
            "columns": tuple(mapping.get("requested_columns", mapping.get("columns", ()))),
            "source": "unknown_mapping",
            "reason": reason or mapping["status"],
            "followup_id": f"mapping:{mapping['item_id']}"}
    return KnownState(state.known, state.unknown + (item,), state.resolved_gaps)

def add_gap(state, gap):
    item = dict(gap); item["source"] = "gap"
    if isinstance(item.get("gap_id"), str):
        item["followup_id"] = f"gap:{item['gap_id']}"
    key = (item.get("item_id"), item.get("span"), item.get("missing"))
    if any((x.get("item_id"), x.get("span"), x.get("missing")) == key for x in state.unknown): return state
    return KnownState(state.known, state.unknown + (item,), state.resolved_gaps)


def initialize_known_state(frame, mappings):
    """Merge post-gap mapping results and semantic gaps into known/unknown.

    Clear-and-allowed mappings are the only initial facts that enter ``known``.
    Gaps and inaccessible or invalid mappings enter the same ``unknown`` queue.
    Unresolved semantic-to-schema mappings stay outside both queues for audit.
    """
    if not isinstance(mappings, dict) or set(mappings) != {"mappings"}:
        raise ValueError("expected validated mappings object")
    state = KnownState()
    for mapping in mappings["mappings"]:
        status = mapping.get("status")
        if status == "clear_and_allowed":
            state = add_mapping(state, mapping)
        elif status == "clear_but_denied":
            state = add_denied(state, mapping)
        elif status == "mapping_unresolved":
            continue
        elif status in {"mapping_invalid", "permission_context_missing"}:
            state = add_unknown_mapping(state, mapping)
        else:
            raise ValueError("unvalidated mapping status")
    for gap in gap_catalog(frame):
        state = add_gap(state, gap)
    return state

def accept_candidates(state, gap_id, candidates, resolve=False):
    if any(not isinstance(x, dict) or not isinstance(x.get("candidate_id"), str) for x in candidates): raise ValueError("invalid candidate")
    old = {x.get("candidate_id") for x in state.known}; additions = []
    for candidate in candidates:
        if candidate["candidate_id"] not in old:
            item = dict(candidate); item.update(source="accepted_candidate", gap_id=gap_id); additions.append(item)
    # An accepted choice resolves the selected unknown immediately.  Keeping it
    # in unknown would cause the deterministic selector to ask the same question
    # again on the next turn.
    remaining_unknown = tuple(item for item in state.unknown if not (
        item.get("gap_id") == gap_id
        or item.get("followup_id") == gap_id
        or item.get("item_id") == gap_id
        or f"gap:{item.get('item_id')}" == gap_id
        or f"denied:{item.get('item_id')}" == gap_id
    ))
    resolved = state.resolved_gaps + ((gap_id,) if gap_id not in state.resolved_gaps else ())
    return KnownState(state.known + tuple(additions), remaining_unknown, resolved)

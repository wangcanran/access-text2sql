"""Deterministic coverage checks for an intent contract."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from .intent_contract import IntentContract, contract_ids


@dataclass(frozen=True)
class CoverageFinding:
    code: str
    message: str
    item_id: str | None = None
    source_phrase: str | None = None


@dataclass(frozen=True)
class CoverageReport:
    complete: bool
    findings: tuple[CoverageFinding, ...] = ()


def _text(contract: IntentContract) -> str:
    parts = [contract.subject.meaning]
    parts.extend(item.meaning for item in contract.outputs)
    parts.extend(item.meaning for item in contract.filters)
    parts.extend(item.meaning for item in contract.groupings)
    if contract.ranking:
        parts.append(contract.ranking.meaning)
    parts.extend(item.meaning for item in contract.constraints)
    parts.extend(item.source_span for item in contract.evidence)
    return " ".join(parts).casefold()


def audit_contract_coverage(request: str, contract: IntentContract) -> CoverageReport:
    """Find high-risk phrases that have no corresponding contract structure.

    This is intentionally a conservative audit, not a parser.  It rejects
    omissions that are cheap to identify deterministically while leaving the
    language interpretation to the model and subsequent user clarification.
    """
    request_text = str(request or "")
    text = request_text.casefold()
    suppressed = {str(item).casefold() for item in (contract.metadata.get("suppressed_semantic_phrases", ()) if isinstance(contract.metadata, Mapping) else ())}
    represented = _text(contract)
    findings: list[CoverageFinding] = []
    output_text = " ".join(item.meaning.casefold() for item in contract.outputs)
    if not contract.outputs:
        findings.append(CoverageFinding("missing_outputs", "request has no output items"))
    ranking_phrase = re.search(r"\b(most common|most frequent|highest|largest|lowest|least|top)\b", text)
    if ranking_phrase and contract.ranking is None:
        findings.append(CoverageFinding("missing_ranking", "ranking or selection phrase is not represented", source_phrase=ranking_phrase.group(0)))
    if ranking_phrase and contract.ranking and not contract.ranking.measure_output_id:
        findings.append(CoverageFinding("ranking_missing_measure", "ranking has no explicit measure", contract.ranking.id, ranking_phrase.group(0)))
    if re.search(r"\b(average|avg|mean)\b", text) and not re.search(r"\b(average|avg|mean)\b", output_text):
        findings.append(CoverageFinding("missing_average_output", "average/mean phrase is not represented by an output", source_phrase="average"))
    if re.search(r"\b(per|each)\b", text) and not any(item.measure and (item.measure.scope_id or item.measure.inner_measure) for item in contract.outputs):
        findings.append(CoverageFinding("missing_grain", "per/each phrase has no explicit measure scope", source_phrase="per/each"))
    if re.search(r"\b(unique|distinct)\b", text) and not any(item.distinct or (item.measure and item.measure.distinct) for item in contract.outputs):
        findings.append(CoverageFinding("missing_distinct", "distinct/unique phrase is not represented", source_phrase="unique/distinct"))
    if re.search(r"\b(label|labels|category|categories)\b", text) and not any(re.search(r"\b(label|labels|category|categories)\b", item.meaning, re.I) for item in contract.outputs) and not any(phrase in text for phrase in suppressed):
        findings.append(CoverageFinding("missing_label_output", "label/category phrase is not represented", source_phrase="label/category"))
    validation = contract.validate()
    findings.extend(CoverageFinding("contract:" + error.split(":", 1)[0], error) for error in validation.errors)
    known = set(contract_ids(contract))
    for item in contract.evidence:
        if item.item_id not in known:
            findings.append(CoverageFinding("evidence_unknown_item", f"evidence references unknown item {item.item_id}", item.item_id))
    return CoverageReport(not findings, tuple(dict.fromkeys(findings)))

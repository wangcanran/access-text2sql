"""Question selection over unresolved intent-contract semantics."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from .contract_coverage import CoverageReport
from .intent_contract import IntentContract


@dataclass(frozen=True)
class ContractQuestion:
    question_id: str
    text: str
    target_ids: tuple[str, ...]
    expected_answer_shape: str = "free_text"
    impact: float = 0.0
    reason: str = ""
    options: tuple[tuple[str, str], ...] = ()
    option_patches: Mapping[str, Mapping[str, Any]] = None  # type: ignore[assignment]
    value_patches: Mapping[str, Mapping[str, Any]] = None  # type: ignore[assignment]
    value_labels: Mapping[str, str] = None  # type: ignore[assignment]

    @property
    def machine_answerable(self) -> bool:
        """Whether the fixed-output simulator can answer without free text."""
        if self.options:
            patches = self.option_patches or {}
            return all(item_id in patches and isinstance(patches[item_id], Mapping) for item_id, _ in self.options)
        if any(isinstance(patch, Mapping) for patch in (self.value_patches or {}).values()):
            return True
        shape = self.expected_answer_shape.casefold()
        if any(token in shape for token in ("confirm", "yes", "no", "boolean")):
            return any(key in (self.value_patches or {}) for key in ("confirm", "deny"))
        return False

    @property
    def fingerprint(self) -> str:
        value = " ".join(self.text.casefold().split()) + "|" + ",".join(sorted(self.target_ids))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _metadata_questions(contract: IntentContract) -> tuple[ContractQuestion, ...]:
    raw = contract.metadata.get("unresolved_questions", ()) if isinstance(contract.metadata, Mapping) else ()
    if not isinstance(raw, (list, tuple)):
        return ()
    result: list[ContractQuestion] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        target = item.get("target_ids", item.get("target_id", ()))
        if isinstance(target, str):
            target = (target,)
        if not isinstance(target, (list, tuple)) or any(not isinstance(value, str) for value in target):
            continue
        text = item.get("question")
        if not isinstance(text, str) or not text.strip():
            continue
        raw_options = item.get("options", ())
        options: list[tuple[str, str]] = []
        patches: dict[str, Mapping[str, Any]] = {}
        if isinstance(raw_options, (list, tuple)):
            for option in raw_options:
                if isinstance(option, Mapping) and isinstance(option.get("id"), str) and isinstance(option.get("label"), str):
                    options.append((option["id"], option["label"]))
                    if isinstance(option.get("patch"), Mapping):
                        patches[option["id"]] = dict(option["patch"])
        values = item.get("value_patches", {})
        value_patches = {str(key): dict(value) for key, value in values.items() if isinstance(value, Mapping)} if isinstance(values, Mapping) else {}
        labels = item.get("value_labels", {})
        value_labels = {str(key): str(value) for key, value in labels.items() if isinstance(value, str)} if isinstance(labels, Mapping) else {}
        result.append(ContractQuestion(f"contract_gap_{index}", text.strip(), tuple(dict.fromkeys(target)), str(item.get("expected_answer_shape", "free_text")), float(item.get("impact", 5.0)), str(item.get("reason", "unresolved_semantic")), tuple(options), patches, value_patches, value_labels))
    return tuple(result)


def _derived_questions(contract: IntentContract, coverage: CoverageReport | None) -> tuple[ContractQuestion, ...]:
    result: list[ContractQuestion] = []
    validation = contract.validate()
    if any(error.startswith("output_not_shared_with_ranking") for error in validation.errors):
        result.append(ContractQuestion("ranking_shared_scope", "Should every requested result be computed for the same selected highest or lowest group?", (contract.ranking.id,) if contract.ranking else ("ranking",), "confirm", 7.5, "ranking_shared_scope_missing", (("same", "yes, use the same selected group for every result"), ("separate", "no, use separate group selections")), {"same": {"unresolved": ()}}))
    if any(error.startswith("ranking_") for error in validation.errors) or (coverage and any(item.code in {"missing_ranking", "ranking_missing_measure"} for item in coverage.findings)):
        options = tuple((item.id, item.meaning) for item in contract.outputs)
        patches = {item.id: {"ranking_measure_output_id": item.id} for item in contract.outputs}
        result.append(ContractQuestion("ranking_measure", "What quantity should determine which result is most common, highest, or lowest?", (contract.ranking.id,) if contract.ranking else ("ranking",), "choice", 7.0, "ranking_measure_missing", options, patches))
    if coverage and any(item.code == "missing_average_output" for item in coverage.findings):
        result.append(ContractQuestion("average_definition", "What should be averaged, and at what level such as per molecule, per customer, or per department?", ("average",), "free_text", 7.0, "average_measure_missing"))
    if coverage and any(item.code == "missing_grain" for item in coverage.findings):
        options = tuple((item.id, item.meaning) for item in contract.scopes)
        patches = {item.id: {"measure_scope_id": item.id} for item in contract.scopes}
        result.append(ContractQuestion("measure_grain", "What entity should define the 'per' or 'each' level of this measure?", ("measure_scope",), "choice", 6.5, "measure_scope_missing", options, patches))
    if coverage and any(item.code == "missing_distinct" for item in coverage.findings):
        options = (("distinct", "count each distinct value once"), ("all", "include every occurrence"))
        result.append(ContractQuestion("distinctness", "Should repeated values be counted once, or should every occurrence be included?", ("distinctness",), "choice", 6.0, "distinctness_missing", options, {"distinct": {"distinct": True}, "all": {"distinct": False}}))
    return tuple(result)


def select_contract_questions(contract: IntentContract, *, coverage: CoverageReport | None = None, asked_fingerprints: tuple[str, ...] = (), limit: int = 1, answerable_only: bool = False) -> tuple[ContractQuestion, ...]:
    """Select finite, high-impact questions without mutating the contract."""
    if limit <= 0:
        return ()
    asked = set(asked_fingerprints)
    candidates = list(_metadata_questions(contract)) + list(_derived_questions(contract, coverage))
    # Unresolved items always outrank presentation preferences.  Stable IDs and
    # fingerprints prevent a malformed answer from causing an endless loop.
    selected: list[ContractQuestion] = []
    seen: set[str] = set()
    for item in sorted(candidates, key=lambda value: (-value.impact, value.question_id)):
        if answerable_only and not item.machine_answerable:
            continue
        if item.fingerprint in asked or item.fingerprint in seen:
            continue
        seen.add(item.fingerprint)
        selected.append(item)
        if len(selected) >= limit:
            break
    return tuple(selected)

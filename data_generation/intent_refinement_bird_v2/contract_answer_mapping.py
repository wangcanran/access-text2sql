"""Apply only allowlisted structured answers to an IntentContract."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .contract_questions import ContractQuestion
from .contract_usersim import ContractUserAnswer
from .intent_contract import IntentContract


def _merge_patch(contract: IntentContract, patch: Mapping[str, Any]) -> IntentContract:
    allowed = {"unresolved", "metadata", "ranking_measure_output_id", "measure_scope_id", "distinct"}
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"answer patch contains unsupported fields: {sorted(unknown)}")
    unresolved = tuple(str(item) for item in patch.get("unresolved", contract.unresolved))
    metadata = dict(contract.metadata)
    if isinstance(patch.get("metadata"), Mapping):
        metadata.update(patch["metadata"])
    updated = replace(contract, unresolved=unresolved, metadata=metadata)
    if "ranking_measure_output_id" in patch:
        if updated.ranking is None:
            raise ValueError("cannot set ranking measure without a ranking")
        updated = replace(updated, ranking=replace(updated.ranking, measure_output_id=str(patch["ranking_measure_output_id"])))
    if "measure_scope_id" in patch:
        outputs = []
        for output in updated.outputs:
            if output.measure and output.measure.inner_measure:
                outputs.append(replace(output, measure=replace(output.measure, inner_measure=replace(output.measure.inner_measure, scope_id=str(patch["measure_scope_id"])))) )
            else:
                outputs.append(output)
        updated = replace(updated, outputs=tuple(outputs))
    if "distinct" in patch:
        outputs = tuple(replace(output, distinct=bool(patch["distinct"])) for output in updated.outputs)
        updated = replace(updated, outputs=outputs)
    return updated


def apply_contract_answer(contract: IntentContract, question: ContractQuestion, answer: ContractUserAnswer) -> IntentContract:
    if answer.decision != "answer":
        raise ValueError("cannot apply a refused contract answer")
    if answer.answer_kind == "select_option":
        if len(answer.selected_option_ids) != 1:
            raise ValueError("contract answer must select exactly one option")
        patch = (question.option_patches or {}).get(answer.selected_option_ids[0])
        if patch is None:
            raise ValueError("selected option has no allowlisted contract patch")
        return _merge_patch(contract, patch)
    if answer.answer_kind == "value_id":
        if not answer.value_id:
            raise ValueError("value_id answer is empty")
        patch = (question.value_patches or {}).get(answer.value_id)
        if patch is None:
            raise ValueError("value ID has no allowlisted contract patch")
        return _merge_patch(contract, patch)
    if answer.answer_kind in {"confirm", "deny"}:
        patch = (question.value_patches or {}).get(answer.answer_kind)
        if patch is None:
            raise ValueError("boolean answer has no allowlisted contract patch")
        return _merge_patch(contract, patch)
    raise ValueError(f"unsupported contract answer kind: {answer.answer_kind}")

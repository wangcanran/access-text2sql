"""Single active v2 runtime façade.

This module owns the orchestration boundary between the public parser, the
evaluator-side user simulator, and the deterministic contract runner.  A live
caller should invoke :class:`ContractRuntime.run`; it must not call the parser
again after a user answer.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .contract_dialogue import (
    CONTRACT_READY,
    CONTRACT_STOP_UNRESOLVED,
    ContractDialogueRunner,
    ContractDialogueSnapshot,
)
from .contract_parse_adapter import ContractParseAdapter
from .contract_parser import ContractParseRequest, parser_public_context
from .contract_usersim import ContractUserQuestion, ContractUserSimulator
from .intent_contract import IntentContract
from .user_renderer import UserRenderer


@dataclass(frozen=True)
class ContractRuntimeResult:
    snapshot: ContractDialogueSnapshot | None
    trace: tuple[dict[str, Any], ...] = ()
    failure: str | None = None

    @property
    def status(self) -> str:
        if self.snapshot is None:
            return self.failure or "contract_runtime_failed"
        return self.snapshot.status


def _with_request(contract: IntentContract, request: str) -> IntentContract:
    metadata = dict(contract.metadata)
    metadata["request"] = request
    return replace(contract, metadata=metadata)


class ContractRuntime:
    """The only active v2 parser/user-simulation/dialogue entry point."""

    def __init__(
        self,
        public_context: Mapping[str, Any],
        parser_client: Any,
        user_client: Any,
        *,
        renderer: UserRenderer | None = None,
        max_turns: int = 8,
        max_no_progress_turns: int = 2,
    ):
        self.public_context = dict(public_context)
        self.parser = ContractParseAdapter(parser_client, max_repair_attempts=1)
        self.user = ContractUserSimulator(user_client)
        self.dialogue = ContractDialogueRunner(
            self.public_context,
            max_turns=max_turns,
            max_no_progress_turns=max_no_progress_turns,
        )
        self.renderer = renderer or UserRenderer(context=self.public_context)
        self.max_turns = max_turns

    def _question(self, question) -> ContractUserQuestion:
        if not question.machine_answerable:
            raise ValueError("active contract question is not fixed-format answerable")
        options = tuple(
            (ident, self.renderer.question(label))
            for ident, label in question.options
            if ident in (question.option_patches or {})
        )
        labels = question.value_labels or {}
        value_labels = {
            str(value_id): self.renderer.question(str(labels.get(value_id, value_id)))
            for value_id in (question.value_patches or {})
        }
        return ContractUserQuestion(
            self.renderer.question(question.text),
            question.expected_answer_shape,
            options,
            value_labels,
        )

    def run(
        self,
        request: str,
        *,
        private_task: Mapping[str, Any],
        history: tuple[dict[str, Any], ...] = (),
    ) -> ContractRuntimeResult:
        """Parse once, then run fixed-answer dialogue until a terminal state."""
        trace: list[dict[str, Any]] = []
        parse_request = ContractParseRequest(
            request,
            parser_public_context(self.public_context),
            history=history,
        )
        parsed = self.parser.parse_safe(parse_request)
        trace.append({"turn": 0, "kind": "contract_parse", "ok": parsed.result is not None})
        if parsed.failure is not None:
            trace[0]["failure"] = parsed.failure.to_dict()
            return ContractRuntimeResult(None, tuple(trace), "contract_parser_failed")
        assert parsed.result is not None
        contract = _with_request(parsed.result.contract, request)
        snapshot = self.dialogue.start(contract, request=request)

        for turn in range(1, self.max_turns + 1):
            question = snapshot.selected_question
            trace.append({
                "turn": turn,
                "status": snapshot.status,
                "question": question.text if question else None,
                "alternatives": [item.id for item in snapshot.alternatives],
            })
            if question is not None:
                try:
                    user_question = self._question(question)
                    answer = self.user.answer_question(user_question, private_task=private_task)
                    trace[-1]["answer_kind"] = answer.answer_kind
                    snapshot = self.dialogue.answer(snapshot, answer, request=request)
                except Exception as exc:
                    trace[-1]["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                    return ContractRuntimeResult(snapshot, tuple(trace), "contract_usersim_error")
                if snapshot.status == CONTRACT_STOP_UNRESOLVED:
                    return ContractRuntimeResult(snapshot, tuple(trace), snapshot.status)
                continue

            if snapshot.status == "NEEDS_PERMISSION_DECISION":
                try:
                    alternatives = (
                        {"id": item.id, "summary": self.renderer.alternative(item.summary)}
                        for item in snapshot.alternatives
                    )
                    decision, selected = self.user.decide_alternative(alternatives, private_task=private_task)
                    trace[-1]["alternative_decision"] = decision
                    if decision == "accept" and selected:
                        snapshot = self.dialogue.accept_alternative(snapshot, selected, request=request)
                        continue
                    snapshot = self.dialogue.reject_alternatives(snapshot)
                    return ContractRuntimeResult(snapshot, tuple(trace), snapshot.status)
                except Exception as exc:
                    trace[-1]["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                    return ContractRuntimeResult(snapshot, tuple(trace), "contract_usersim_error")
            break

        failure = None if snapshot.status == CONTRACT_READY else snapshot.status
        return ContractRuntimeResult(snapshot, tuple(trace), failure)

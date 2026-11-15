"""Deterministic dialogue state derived from an intent contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .contract_coverage import CoverageReport, audit_contract_coverage
from .contract_obligations import ContractObligations, compile_contract_obligations
from .contract_permission import ContractPermissionDiagnostic, check_contract_permissions
from .contract_questions import ContractQuestion, select_contract_questions
from .contract_readiness import ContractReadiness, assess_contract_readiness
from .intent_contract import IntentContract
from .contract_alternatives import ContractAlternative, generate_contract_alternatives, apply_contract_alternative
from .contract_answer_mapping import apply_contract_answer
from .contract_usersim import ContractUserAnswer


CONTRACT_NEEDS_CLARIFICATION = "NEEDS_SEMANTIC_CLARIFICATION"
CONTRACT_NEEDS_BINDING = "NEEDS_SCHEMA_BINDING"
CONTRACT_INVALID_BINDING = "INVALID_SCHEMA_BINDING"
CONTRACT_NEEDS_PERMISSION = "NEEDS_PERMISSION_DECISION"
CONTRACT_READY = "READY"
CONTRACT_SAFE_REFUSAL = "SAFE_REFUSAL"
CONTRACT_STOP_UNRESOLVED = "STOP_UNRESOLVED"


@dataclass(frozen=True)
class ContractTurn:
    turn: int
    question: ContractQuestion
    answer: str
    progress: bool


@dataclass(frozen=True)
class ContractDialogueSnapshot:
    contract: IntentContract
    coverage: CoverageReport
    obligations: ContractObligations
    permission_diagnostics: tuple[ContractPermissionDiagnostic, ...]
    readiness: ContractReadiness
    status: str
    selected_question: ContractQuestion | None = None
    asked_fingerprints: tuple[str, ...] = ()
    turns: tuple[ContractTurn, ...] = ()
    no_progress_turns: int = 0
    alternatives: tuple[ContractAlternative, ...] = ()


class ContractDialogueRunner:
    def __init__(self, public_context: Mapping[str, object], *, max_turns: int = 8, max_no_progress_turns: int = 2):
        if max_turns <= 0 or max_no_progress_turns < 0:
            raise ValueError("dialogue budgets must be positive")
        self.public_context = dict(public_context)
        self.max_turns = max_turns
        self.max_no_progress_turns = max_no_progress_turns

    def _derive(self, contract: IntentContract, *, request: str, asked: tuple[str, ...], turns: tuple[ContractTurn, ...], no_progress: int) -> ContractDialogueSnapshot:
        coverage = audit_contract_coverage(request, contract)
        obligations = compile_contract_obligations(contract)
        permission = check_contract_permissions(contract, self.public_context, obligations)
        readiness = assess_contract_readiness(contract, coverage=coverage, permission_diagnostics=permission)
        all_questions = select_contract_questions(contract, coverage=coverage, asked_fingerprints=asked)
        questions = select_contract_questions(contract, coverage=coverage, asked_fingerprints=asked, answerable_only=True)
        question = questions[0] if questions else None
        alternatives = generate_contract_alternatives(contract, permission)
        if len(turns) >= self.max_turns or no_progress > self.max_no_progress_turns:
            status = CONTRACT_STOP_UNRESOLVED if not readiness.semantic_ready else CONTRACT_SAFE_REFUSAL
            question = None
        elif not readiness.semantic_ready and question is not None:
            status = CONTRACT_NEEDS_CLARIFICATION
        elif not readiness.semantic_ready:
            status = CONTRACT_STOP_UNRESOLVED
        elif all_questions and question is None:
            # The remaining ambiguity exists, but no fixed-format patch can
            # represent its answer.  Do not silently turn an unanswerable
            # free-text question into READY.
            status = CONTRACT_STOP_UNRESOLVED
        elif any(item.code == "binding_required" for item in permission):
            status = CONTRACT_NEEDS_BINDING
        elif any(item.code == "invalid_field" for item in permission):
            status = CONTRACT_INVALID_BINDING
        elif any(item.code == "invalid_relation" for item in permission):
            status = CONTRACT_INVALID_BINDING
        elif any(item.code == "permission_denied" for item in permission):
            status = CONTRACT_NEEDS_PERMISSION
        else:
            status = CONTRACT_READY
        return ContractDialogueSnapshot(contract, coverage, obligations, permission, readiness, status, question, asked, turns, no_progress, alternatives if status == CONTRACT_NEEDS_PERMISSION else ())

    @staticmethod
    def _stop_unresolved(snapshot: ContractDialogueSnapshot, answer: ContractUserAnswer) -> ContractDialogueSnapshot:
        question = snapshot.selected_question
        if question is None:
            raise ValueError("there is no pending contract question")
        turn = ContractTurn(len(snapshot.turns) + 1, question, str(answer.answer).strip(), False)
        return ContractDialogueSnapshot(
            snapshot.contract, snapshot.coverage, snapshot.obligations,
            snapshot.permission_diagnostics, snapshot.readiness,
            CONTRACT_STOP_UNRESOLVED, None, snapshot.asked_fingerprints,
            (*snapshot.turns, turn), snapshot.no_progress_turns + 1,
            snapshot.alternatives,
        )

    def start(self, contract: IntentContract, *, request: str | None = None) -> ContractDialogueSnapshot:
        source = request or str(contract.metadata.get("request", ""))
        return self._derive(contract, request=source, asked=(), turns=(), no_progress=0)

    def incorporate(self, snapshot: ContractDialogueSnapshot, contract: IntentContract, *, request: str | None = None, answer: str = "", progress: bool = True) -> ContractDialogueSnapshot:
        """Legacy migration hook; active contract smoke uses :meth:`answer`.

        Keeping this method avoids breaking older tests and callers while the
        v2 runtime moves to the fixed-answer protocol.  It must not be used by
        a live path because it accepts a replacement contract directly.
        """
        if snapshot.selected_question is None:
            raise ValueError("there is no pending contract question")
        question = snapshot.selected_question
        turn = ContractTurn(len(snapshot.turns) + 1, question, str(answer).strip(), progress)
        asked = tuple(dict.fromkeys((*snapshot.asked_fingerprints, question.fingerprint)))
        no_progress = 0 if progress else snapshot.no_progress_turns + 1
        source = request or str(contract.metadata.get("request", ""))
        return self._derive(contract, request=source, asked=asked, turns=(*snapshot.turns, turn), no_progress=no_progress)

    def answer(self, snapshot: ContractDialogueSnapshot, answer: ContractUserAnswer, *, request: str | None = None) -> ContractDialogueSnapshot:
        """Apply one fixed-format user answer and recompute the dialogue state.

        The user simulator returns an opaque option/value id.  The runner is
        the only component allowed to turn that id into a contract update.
        This keeps natural-language user text out of the semantic state and
        prevents a response from implicitly confirming every node it mentions.
        """
        if snapshot.selected_question is None:
            raise ValueError("there is no pending contract question")
        if not isinstance(answer, ContractUserAnswer):
            raise TypeError("answer must be a ContractUserAnswer")
        question = snapshot.selected_question
        if answer.decision != "answer":
            return self._stop_unresolved(snapshot, answer)
        try:
            updated = apply_contract_answer(snapshot.contract, question, answer)
        except (TypeError, ValueError):
            # A structurally valid response can still name an option/value
            # that was not offered.  Treat that exactly like an unanswerable
            # BIRD-Interact turn; never let it mutate or complete the contract.
            return self._stop_unresolved(snapshot, answer)
        asked = tuple(dict.fromkeys((*snapshot.asked_fingerprints, question.fingerprint)))
        turn = ContractTurn(len(snapshot.turns) + 1, question, answer.answer.strip(), True)
        source = request or str(updated.metadata.get("request", ""))
        return self._derive(updated, request=source, asked=asked, turns=(*snapshot.turns, turn), no_progress=0)

    def accept_alternative(self, snapshot: ContractDialogueSnapshot, alternative_id: str, *, request: str | None = None) -> ContractDialogueSnapshot:
        alternative = next((item for item in snapshot.alternatives if item.id == alternative_id), None)
        if alternative is None:
            raise ValueError("alternative was not offered in this snapshot")
        updated = apply_contract_alternative(snapshot.contract, alternative)
        return self._derive(updated, request=request or str(updated.metadata.get("request", "")), asked=snapshot.asked_fingerprints, turns=snapshot.turns, no_progress=0)

    def reject_alternatives(self, snapshot: ContractDialogueSnapshot) -> ContractDialogueSnapshot:
        return ContractDialogueSnapshot(snapshot.contract, snapshot.coverage, snapshot.obligations, snapshot.permission_diagnostics, snapshot.readiness, CONTRACT_SAFE_REFUSAL, None, snapshot.asked_fingerprints, snapshot.turns, snapshot.no_progress_turns, ())

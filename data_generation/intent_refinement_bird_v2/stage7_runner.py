"""Stage-7 execution pipeline.

The runner is the enforcement point between an authorized intent graph and
Text2SQL.  Gold/pseudo SQL are accepted only as optional offline evaluation
inputs after generation; they are never included in the model payload.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

from .final_request_renderer import FinalRequestRenderer
from .intent_graph import IntentGraph
from .result_compare import ResultComparison, compare_sql_results
from .sql_validator import SQLValidation, validate_sql, validate_contract_sql
from .text2sql_adapter import Text2SQLAdapter
from .user_renderer import UserRenderer
from .plan_validator import validate_plan
from .semantic_readiness import assess_semantic_readiness
from .intent_graph import Hypothesis, HypothesisSet
from .intent_contract import IntentContract
from .contract_coverage import audit_contract_coverage
from .contract_permission import check_contract_permissions
from .contract_readiness import assess_contract_readiness
from .contract_renderer import render_contract_request


@dataclass(frozen=True)
class Stage7Run:
    final_request: str
    sql: str | None
    validation: SQLValidation | None
    result_comparison: ResultComparison | None = None
    failure: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.validation is not None and self.validation.allowed and (self.result_comparison is None or self.result_comparison.equivalent)


class Stage7Runner:
    def __init__(self, public_context: Mapping[str, Any], text2sql: Text2SQLAdapter, connection: sqlite3.Connection | None = None, *, renderer: UserRenderer | None = None):
        self.public_context = dict(public_context)
        self.text2sql = text2sql
        self.connection = connection
        self.renderer = renderer or UserRenderer(context=public_context)
        self.final_renderer = FinalRequestRenderer(self.renderer)

    def run(self, graph: IntentGraph, *, witness_sql: str | None = None, ordered_results: bool = False) -> Stage7Run:
        plan = validate_plan(graph, self.public_context)
        hypotheses = HypothesisSet().add(Hypothesis("execution_current", graph, 1.0, ()))
        readiness = assess_semantic_readiness(graph, hypotheses)
        if not readiness.ready:
            return Stage7Run("", None, None, failure="intent_not_ready")
        if not plan.allowed:
            return Stage7Run("", None, None, failure=f"intent_validation_failed:{plan.status}")
        final_request = self.final_renderer.render(graph)
        try:
            sql = self.text2sql.generate(final_request, self.public_context)
        except Exception as exc:
            return Stage7Run(final_request, None, None, failure=f"text2sql_error:{type(exc).__name__}")
        validation = validate_sql(sql, graph, self.public_context, connection=self.connection)
        if not validation.allowed:
            return Stage7Run(final_request, sql, validation, failure="sql_validation_failed")
        comparison = None
        if witness_sql is not None:
            if self.connection is None:
                return Stage7Run(final_request, sql, validation, failure="witness_comparison_requires_connection")
            comparison = compare_sql_results(self.connection, sql, witness_sql, ordered=ordered_results)
        return Stage7Run(final_request, sql, validation, comparison, None if comparison is None or comparison.equivalent else "result_mismatch")

    def run_contract(self, contract: IntentContract, *, witness_sql: str | None = None, ordered_results: bool = False) -> Stage7Run:
        """Run the contract path without manufacturing a complete intent graph.

        This is the migration entry point.  Semantic readiness and permission
        diagnostics are derived by rules; only an authorized/allowed contract
        reaches Text2SQL.  SQL validation still uses the existing graph path
        until the obligation-aware validator is migrated.
        """
        coverage = audit_contract_coverage(contract.metadata.get("request", ""), contract) if contract.metadata.get("request") else None
        permission = check_contract_permissions(contract, self.public_context)
        readiness = assess_contract_readiness(contract, coverage=coverage, permission_diagnostics=permission)
        if not readiness.semantic_ready:
            return Stage7Run("", None, None, failure="contract_not_ready")
        if any(item.code == "binding_required" for item in permission):
            return Stage7Run("", None, None, failure="contract_binding_required")
        if not readiness.executable_ready or permission:
            return Stage7Run("", None, None, failure="contract_permission_denied")
        final_request = render_contract_request(contract, renderer=self.renderer)
        try:
            sql = self.text2sql.generate(final_request, self.public_context)
        except Exception as exc:
            return Stage7Run(final_request, None, None, failure=f"text2sql_error:{type(exc).__name__}")
        # The contract path has no graph to pass to the legacy obligation
        # checker.  It still enforces SQL syntax, schema, permissions and FK
        # connectivity; semantic obligation matching is added in the next
        # migration step once the Text2SQL validator accepts contracts.
        empty_graph = IntentGraph()
        validation = validate_contract_sql(sql, contract, self.public_context, connection=self.connection)
        if not validation.allowed:
            return Stage7Run(final_request, sql, validation, failure="sql_validation_failed")
        comparison = None
        if witness_sql is not None:
            if self.connection is None:
                return Stage7Run(final_request, sql, validation, failure="witness_comparison_requires_connection")
            comparison = compare_sql_results(self.connection, sql, witness_sql, ordered=ordered_results)
        return Stage7Run(final_request, sql, validation, comparison, None if comparison is None or comparison.equivalent else "result_mismatch")

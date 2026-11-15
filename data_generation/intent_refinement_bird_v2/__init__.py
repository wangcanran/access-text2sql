"""Stage-0 public/private boundary and BIRD fixtures.

This package intentionally does not import the legacy intent-refinement
implementation.  Later stages build on these data contracts.
"""

from .contracts import EvaluationCase, PublicCase
from .fixtures import (
    build_evaluation_case,
    build_public_case,
    extract_foreign_keys,
    extract_sqlite_schema,
    load_jsonl_cases,
    normalize_policy,
    public_agent_payload,
)
from .evidence import Authorization, Evidence
from .transformation import SemanticTransformation
from .parser_contract import (
    ALTERNATIVE_RESPONSE_SCHEMA, ASK_RESPONSE_SCHEMA, PARSE_UPDATE_RESPONSE_SCHEMA,
    AlternativeProposal, AgentActionResponse, ClarificationRequest,
    ParseRequest, ParseResponse, parse_agent_action_response,
)
from .parser_adapter import ParseAdapter, ParseResult
from .parse_errors import ParseAttempt, ParseFailure
from .intent_graph import (
    Constraint,
    ConstraintLedger,
    Hypothesis,
    HypothesisSet,
    IntentEdge,
    IntentGraph,
    IntentNode,
    SchemaBinding,
)
from .dependencies import ExecutionObligation, collect_execution_obligations
from .binding import BindingDiagnostic, validate_graph_bindings
from .permission_checker import PermissionDiagnostic, check_permissions
from .plan_validator import PlanValidation, validate_hypotheses, validate_plan
from .question_selector import QuestionCandidate, question_fingerprint, select_questions
from .state_deriver import (
    DerivedState, STATE_BLOCKED_BY_PERMISSION, STATE_NEEDS_DISAMBIGUATION,
    STATE_READY_AUTHORIZED_ALTERNATIVE, STATE_READY_ORIGINAL,
    STATE_SAFE_REFUSAL, STATE_STOP_UNRESOLVED,
    STATE_WAITING_FOR_ALTERNATIVE_AUTHORIZATION, derive_state,
)
from .dialogue_runner import DialogueRunner, DialogueSnapshot, DialogueTurn
from .transformations import apply_transformation, graph_semantics, make_transformation, node_semantics
from .alternative_generator import AlternativeCandidate, generate_alternatives
from .authorization import AuthorizationResult, authorize_transformation, filter_rejected, present_transformations, reject_transformation
from .profile_policy import AlternativeDecision, ProfileName, choose_alternative
from .usersim import OfflineUserSimulator, SimulatorPayload, UserResponse, build_user_simulator
from .description_catalog import ColumnDescription, DescriptionCatalog, load_bird_description_catalog
from .user_renderer import UserRenderer, renderer_for_context
from .llm_usersim import (
    ALTERNATIVE_SCHEMA as USER_ALTERNATIVE_SCHEMA,
    CLARIFICATION_SCHEMA as USER_CLARIFICATION_SCHEMA,
    LOCATOR_SCHEMA as USER_LOCATOR_SCHEMA,
    LLMUserResponse, LLMUserSimulator, UserModelClient, UserQuestion,
    build_llm_user_simulator,
)
from .final_request_renderer import FinalRequestRenderer, render_final_request
from .text2sql_adapter import Text2SQLAdapter, Text2SQLClient, TEXT2SQL_RESPONSE_SCHEMA
from .sql_validator import SQLDiagnostic, SQLValidation, validate_contract_sql, validate_sql
from .result_compare import QueryResult, ResultComparison, compare_results, compare_sql_results, execute_query
from .stage7_runner import Stage7Run, Stage7Runner
from .llm_client import OpenAIJSONClient, OpenAITextClient
from .evaluate import CaseEvaluation, TraceEvent, evaluate_case, evaluate_matrix
from .metrics import EvaluationMetrics, aggregate_metrics
from .report import build_report, write_report
from .v7_inputs import V7LoadStats, load_bird_role_rows, load_bird_v7_cases, load_v7_evaluation_cases, renderer_for_bird_v7_case, v7_all_columns_policy
from .semantic_readiness import SemanticReadiness, assess_semantic_readiness
from .parser_contract import validate_graph_structure
from .intent_contract import (
    ContractValidation, FilterSpec, GroupingSpec, IntentContract, MeasureSpec,
    OutputSpec, RankingSpec, RelationSpec, ScopeSpec, SemanticConstraint,
    SemanticEvidence, SubjectSpec, contract_ids,
    parse_intent_contract,
)
from .contract_obligations import ContractObligation, ContractObligations, compile_contract_obligations
from .contract_coverage import CoverageFinding, CoverageReport, audit_contract_coverage
from .contract_parser import CONTRACT_PARSER_PROMPT, CONTRACT_RESPONSE_SCHEMA, ContractParseRequest, ContractParseResult, parse_contract, parse_contract_response, parser_public_context
from .contract_permission import ContractPermissionDiagnostic, check_contract_permissions
from .contract_readiness import ContractReadiness, assess_contract_readiness
from .contract_renderer import render_contract_request
from .contract_questions import ContractQuestion, select_contract_questions
from .contract_dialogue import (
    CONTRACT_INVALID_BINDING, CONTRACT_NEEDS_BINDING, CONTRACT_NEEDS_CLARIFICATION, CONTRACT_NEEDS_PERMISSION, CONTRACT_READY,
    CONTRACT_SAFE_REFUSAL, CONTRACT_STOP_UNRESOLVED, ContractDialogueRunner,
    ContractDialogueSnapshot, ContractTurn,
)
from .contract_parse_adapter import ContractParseAdapter, ContractParseOutcome
from .contract_usersim import CONTRACT_ALTERNATIVE_PROMPT, CONTRACT_ALTERNATIVE_RESPONSE_SCHEMA, CONTRACT_USER_PROMPT, CONTRACT_USER_RESPONSE_SCHEMA, ContractUserAnswer, ContractUserQuestion, ContractUserSimulator, answer_contract_question, decide_contract_alternative
from .contract_alternatives import ContractAlternative, apply_contract_alternative, generate_contract_alternatives
from .contract_answer_mapping import apply_contract_answer
from .contract_runtime import ContractRuntime, ContractRuntimeResult
from .contract_evaluate import contract_result_to_evaluation
from .contract_binding import resolve_contract_bindings
from .pseudo_sql_sketch import (
    V7_PLACEHOLDER_CATEGORIES, PseudoSQLPlaceholder, PseudoSQLIssue,
    PseudoSQLSketch, PseudoSQLParseResult, PseudoSQLEvaluation,
    parse_pseudo_sql, evaluate_pseudo_sql,
)

__all__ = [
    "EvaluationCase",
    "PublicCase",
    "build_evaluation_case",
    "build_public_case",
    "extract_foreign_keys",
    "extract_sqlite_schema",
    "load_jsonl_cases",
    "normalize_policy",
    "public_agent_payload",
    "Authorization",
    "Evidence",
    "Constraint",
    "ConstraintLedger",
    "Hypothesis",
    "HypothesisSet",
    "IntentEdge",
    "IntentGraph",
    "IntentNode",
    "SchemaBinding",
    "SemanticTransformation",
    "ParseRequest",
    "ParseResponse",
    "ClarificationRequest",
    "AlternativeProposal",
    "AgentActionResponse",
    "parse_agent_action_response",
    "PARSE_UPDATE_RESPONSE_SCHEMA",
    "ASK_RESPONSE_SCHEMA",
    "ALTERNATIVE_RESPONSE_SCHEMA",
    "ParseAdapter",
    "ParseResult",
    "ParseAttempt",
    "ParseFailure",
    "ExecutionObligation",
    "collect_execution_obligations",
    "BindingDiagnostic",
    "validate_graph_bindings",
    "PermissionDiagnostic",
    "check_permissions",
    "PlanValidation",
    "validate_plan",
    "validate_hypotheses",
    "QuestionCandidate",
    "question_fingerprint",
    "select_questions",
    "DerivedState",
    "derive_state",
    "STATE_NEEDS_DISAMBIGUATION",
    "STATE_BLOCKED_BY_PERMISSION",
    "STATE_WAITING_FOR_ALTERNATIVE_AUTHORIZATION",
    "STATE_READY_ORIGINAL",
    "STATE_READY_AUTHORIZED_ALTERNATIVE",
    "STATE_SAFE_REFUSAL",
    "STATE_STOP_UNRESOLVED",
    "DialogueRunner",
    "DialogueSnapshot",
    "DialogueTurn",
    "AlternativeCandidate",
    "generate_alternatives",
    "apply_transformation",
    "graph_semantics",
    "make_transformation",
    "node_semantics",
    "AuthorizationResult",
    "authorize_transformation",
    "present_transformations",
    "reject_transformation",
    "filter_rejected",
    "ProfileName",
    "AlternativeDecision",
    "choose_alternative",
    "OfflineUserSimulator",
    "SimulatorPayload",
    "UserResponse",
    "build_user_simulator",
    "ColumnDescription",
    "DescriptionCatalog",
    "load_bird_description_catalog",
    "UserRenderer",
    "renderer_for_context",
    "LLMUserResponse",
    "LLMUserSimulator",
    "UserModelClient",
    "UserQuestion",
    "build_llm_user_simulator",
    "USER_LOCATOR_SCHEMA",
    "USER_CLARIFICATION_SCHEMA",
    "USER_ALTERNATIVE_SCHEMA",
    "FinalRequestRenderer",
    "render_final_request",
    "Text2SQLAdapter",
    "Text2SQLClient",
    "TEXT2SQL_RESPONSE_SCHEMA",
    "SQLDiagnostic",
    "SQLValidation",
    "validate_sql",
    "validate_contract_sql",
    "QueryResult",
    "ResultComparison",
    "compare_results",
    "compare_sql_results",
    "execute_query",
    "Stage7Run",
    "Stage7Runner",
    "OpenAIJSONClient",
    "OpenAITextClient",
    "CaseEvaluation",
    "TraceEvent",
    "evaluate_case",
    "evaluate_matrix",
    "EvaluationMetrics",
    "aggregate_metrics",
    "build_report",
    "write_report",
    "V7LoadStats",
    "load_bird_role_rows",
    "load_bird_v7_cases",
    "renderer_for_bird_v7_case",
    "load_v7_evaluation_cases",
    "v7_all_columns_policy",
    "SemanticReadiness",
    "assess_semantic_readiness",
    "validate_graph_structure",
    "IntentContract",
    "SubjectSpec",
    "ScopeSpec",
    "MeasureSpec",
    "OutputSpec",
    "FilterSpec",
    "GroupingSpec",
    "RankingSpec",
    "RelationSpec",
    "SemanticConstraint",
    "SemanticEvidence",
    "ContractValidation",
    "contract_ids",
    "parse_intent_contract",
    "ContractObligation",
    "ContractObligations",
    "compile_contract_obligations",
    "CoverageFinding",
    "CoverageReport",
    "audit_contract_coverage",
    "ContractParseRequest",
    "ContractParseResult",
    "CONTRACT_RESPONSE_SCHEMA",
    "CONTRACT_PARSER_PROMPT",
    "parse_contract",
    "parse_contract_response",
    "parser_public_context",
    "ContractPermissionDiagnostic",
    "check_contract_permissions",
    "ContractReadiness",
    "assess_contract_readiness",
    "render_contract_request",
    "ContractQuestion",
    "select_contract_questions",
    "ContractDialogueRunner",
    "ContractDialogueSnapshot",
    "ContractTurn",
    "CONTRACT_NEEDS_CLARIFICATION",
    "CONTRACT_NEEDS_BINDING",
    "CONTRACT_INVALID_BINDING",
    "CONTRACT_NEEDS_PERMISSION",
    "CONTRACT_READY",
    "CONTRACT_SAFE_REFUSAL",
    "CONTRACT_STOP_UNRESOLVED",
    "ContractParseAdapter",
    "ContractParseOutcome",
    "ContractUserQuestion",
    "ContractUserAnswer",
    "ContractUserSimulator",
    "CONTRACT_USER_RESPONSE_SCHEMA",
    "CONTRACT_USER_PROMPT",
    "answer_contract_question",
    "decide_contract_alternative",
    "CONTRACT_ALTERNATIVE_RESPONSE_SCHEMA",
    "CONTRACT_ALTERNATIVE_PROMPT",
    "ContractAlternative",
    "generate_contract_alternatives",
    "apply_contract_alternative",
    "apply_contract_answer",
    "ContractRuntime",
    "ContractRuntimeResult",
    "contract_result_to_evaluation",
    "resolve_contract_bindings",
    "V7_PLACEHOLDER_CATEGORIES",
    "PseudoSQLPlaceholder",
    "PseudoSQLIssue",
    "PseudoSQLSketch",
    "PseudoSQLParseResult",
    "PseudoSQLEvaluation",
    "parse_pseudo_sql",
    "evaluate_pseudo_sql",
]

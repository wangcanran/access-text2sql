"""Immutable semantic intent graph and hypothesis/constraint snapshots."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Literal, Mapping

from .evidence import Authorization, Evidence
from .transformation import SemanticTransformation


NodeStatus = Literal["proposed", "unresolved", "confirmed", "blocked", "rejected"]
PermissionState = Literal["unknown", "allowed", "denied", "partial", "not_applicable"]
EdgeKind = Literal["dependency", "binding", "relation", "alternative"]

_NODE_TYPES = {
    "TargetEntity", "OutputExpression", "FilterPredicate", "Aggregation",
    "MeasureExpression", "Grouping", "Ordering", "Ranking", "TopK",
    "ResultScope", "JoinRelation", "Subquery",
}
_NODE_STATUSES = {"proposed", "unresolved", "confirmed", "blocked", "rejected"}
_PERMISSION_STATES = {"unknown", "allowed", "denied", "partial", "not_applicable"}
_EDGE_KINDS = {"dependency", "binding", "relation", "alternative"}


def _freeze_value(value: Any) -> Any:
    """Recursively freeze JSON-like payloads stored in immutable snapshots."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    """Convert frozen JSON-like values back to JSON-compatible values."""
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset)):
        return [_thaw_value(item) for item in value]
    return value


@dataclass(frozen=True)
class SchemaBinding:
    """A possible physical schema binding for a business-level node."""

    table: str
    column: str
    confidence: float = 0.0
    evidence: tuple[Evidence, ...] = ()
    valid: bool = True
    validation_reason: str = ""

    def __post_init__(self) -> None:
        if not self.table.strip() or not self.column.strip():
            raise ValueError("schema binding table and column must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("binding confidence must be between 0 and 1")
        if self.valid and self.validation_reason:
            raise ValueError("a valid binding cannot have a validation reason")

    @property
    def qualified_name(self) -> str:
        return f"{self.table}.{self.column}"

    def with_update(self, **changes: Any) -> "SchemaBinding":
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "column": self.column,
            "confidence": self.confidence,
            "evidence": [item.to_dict() for item in self.evidence],
            "valid": self.valid,
            "validation_reason": self.validation_reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SchemaBinding":
        return cls(
            table=str(value["table"]),
            column=str(value["column"]),
            confidence=float(value.get("confidence", 0.0)),
            evidence=tuple(Evidence.from_dict(item) for item in value.get("evidence", ())),
            valid=bool(value.get("valid", True)),
            validation_reason=str(value.get("validation_reason", "")),
        )


@dataclass(frozen=True)
class IntentNode:
    node_id: str
    semantic_type: str
    business_meaning: str
    schema_bindings: tuple[SchemaBinding, ...] = ()
    value: Any = None
    operator: str | None = None
    dependencies: tuple[str, ...] = ()
    user_evidence: tuple[Evidence, ...] = ()
    confidence: float = 0.0
    permission_state: PermissionState = "unknown"
    status: NodeStatus = "proposed"
    attributes: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.node_id.strip() or not self.business_meaning.strip():
            raise ValueError("node_id and business_meaning must not be empty")
        if self.semantic_type not in _NODE_TYPES:
            raise ValueError(f"unsupported semantic node type: {self.semantic_type}")
        if self.status not in _NODE_STATUSES:
            raise ValueError(f"unsupported node status: {self.status}")
        if self.permission_state not in _PERMISSION_STATES:
            raise ValueError(f"unsupported permission state: {self.permission_state}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("node confidence must be between 0 and 1")
        object.__setattr__(self, "value", _freeze_value(self.value))
        object.__setattr__(self, "attributes", _freeze_value(self.attributes))
        if self.status == "confirmed" and not any(item.is_user for item in self.user_evidence):
            raise ValueError("confirmed nodes require user evidence")

    @property
    def confirmed_by_user(self) -> bool:
        return any(item.is_user for item in self.user_evidence)

    def with_update(self, **changes: Any) -> "IntentNode":
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "semantic_type": self.semantic_type,
            "business_meaning": self.business_meaning,
            "schema_bindings": [item.to_dict() for item in self.schema_bindings],
            "value": _thaw_value(self.value),
            "operator": self.operator,
            "dependencies": list(self.dependencies),
            "user_evidence": [item.to_dict() for item in self.user_evidence],
            "confidence": self.confidence,
            "permission_state": self.permission_state,
            "status": self.status,
            "attributes": _thaw_value(self.attributes),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IntentNode":
        return cls(
            node_id=str(value["id"]),
            semantic_type=str(value["semantic_type"]),
            business_meaning=str(value["business_meaning"]),
            schema_bindings=tuple(SchemaBinding.from_dict(item) for item in value.get("schema_bindings", ())),
            value=value.get("value"),
            operator=value.get("operator"),
            dependencies=tuple(str(item) for item in value.get("dependencies", ())),
            user_evidence=tuple(Evidence.from_dict(item) for item in value.get("user_evidence", ())),
            confidence=float(value.get("confidence", 0.0)),
            permission_state=str(value.get("permission_state", "unknown")),
            status=str(value.get("status", "proposed")),
            attributes=value.get("attributes", {}),
        )


@dataclass(frozen=True)
class IntentEdge:
    edge_id: str
    source_id: str
    target_id: str
    kind: EdgeKind
    label: str = ""

    def __post_init__(self) -> None:
        if not self.edge_id.strip() or not self.source_id.strip() or not self.target_id.strip():
            raise ValueError("edge ids and endpoints must not be empty")
        if self.kind not in _EDGE_KINDS:
            raise ValueError(f"unsupported edge kind: {self.kind}")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.edge_id, "source": self.source_id, "target": self.target_id, "kind": self.kind, "label": self.label}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IntentEdge":
        return cls(str(value["id"]), str(value["source"]), str(value["target"]), str(value["kind"]), str(value.get("label", "")))


@dataclass(frozen=True)
class IntentGraph:
    nodes: tuple[IntentNode, ...] = ()
    edges: tuple[IntentEdge, ...] = ()
    version: str = "semantic-intent-graph-v1"

    def __post_init__(self) -> None:
        node_ids = [node.node_id for node in self.nodes]
        edge_ids = [edge.edge_id for edge in self.edges]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("intent node ids must be unique")
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("intent edge ids must be unique")
        node_set = set(node_ids)
        for node in self.nodes:
            unknown = set(node.dependencies) - node_set
            if unknown:
                raise ValueError(f"node {node.node_id} has unknown dependencies: {sorted(unknown)}")
        for edge in self.edges:
            if edge.source_id not in node_set or edge.target_id not in node_set:
                raise ValueError(f"edge {edge.edge_id} references an unknown node")

    def node(self, node_id: str) -> IntentNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(node_id)

    def add_node(self, node: IntentNode) -> "IntentGraph":
        if any(item.node_id == node.node_id for item in self.nodes):
            raise ValueError(f"duplicate node id: {node.node_id}")
        return replace(self, nodes=self.nodes + (node,))

    def update_node(self, node: IntentNode) -> "IntentGraph":
        if not any(item.node_id == node.node_id for item in self.nodes):
            raise KeyError(node.node_id)
        return replace(self, nodes=tuple(node if item.node_id == node.node_id else item for item in self.nodes))

    def add_edge(self, edge: IntentEdge) -> "IntentGraph":
        if any(item.edge_id == edge.edge_id for item in self.edges):
            raise ValueError(f"duplicate edge id: {edge.edge_id}")
        return replace(self, edges=self.edges + (edge,))

    def unresolved_nodes(self) -> tuple[IntentNode, ...]:
        return tuple(item for item in self.nodes if item.status in {"proposed", "unresolved", "blocked"})

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "nodes": [item.to_dict() for item in self.nodes], "edges": [item.to_dict() for item in self.edges]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "IntentGraph":
        return cls(
            nodes=tuple(IntentNode.from_dict(item) for item in value.get("nodes", ())),
            edges=tuple(IntentEdge.from_dict(item) for item in value.get("edges", ())),
            version=str(value.get("version", "semantic-intent-graph-v1")),
        )


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    graph: IntentGraph
    score: float = 0.0
    unresolved_node_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.hypothesis_id.strip():
            raise ValueError("hypothesis_id must not be empty")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("hypothesis score must be between 0 and 1")
        known = {node.node_id for node in self.graph.nodes}
        if not set(self.unresolved_node_ids) <= known:
            raise ValueError("unresolved hypothesis node is not present in graph")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.hypothesis_id, "graph": self.graph.to_dict(), "score": self.score, "unresolved_node_ids": list(self.unresolved_node_ids), "diagnostics": list(self.diagnostics)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Hypothesis":
        return cls(str(value["id"]), IntentGraph.from_dict(value["graph"]), float(value.get("score", 0.0)), tuple(value.get("unresolved_node_ids", ())), tuple(value.get("diagnostics", ())))


@dataclass(frozen=True)
class HypothesisSet:
    hypotheses: tuple[Hypothesis, ...] = ()
    version: str = "semantic-hypotheses-v1"

    def __post_init__(self) -> None:
        ids = [item.hypothesis_id for item in self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError("hypothesis ids must be unique")

    def add(self, hypothesis: Hypothesis) -> "HypothesisSet":
        if any(item.hypothesis_id == hypothesis.hypothesis_id for item in self.hypotheses):
            raise ValueError(f"duplicate hypothesis id: {hypothesis.hypothesis_id}")
        return replace(self, hypotheses=self.hypotheses + (hypothesis,))

    def best(self) -> Hypothesis | None:
        return max(self.hypotheses, key=lambda item: item.score, default=None)

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "hypotheses": [item.to_dict() for item in self.hypotheses]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HypothesisSet":
        return cls(tuple(Hypothesis.from_dict(item) for item in value.get("hypotheses", ())), str(value.get("version", "semantic-hypotheses-v1")))


@dataclass(frozen=True)
class Constraint:
    constraint_id: str
    kind: str
    subject_id: str
    value: Any
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.constraint_id.strip() or not self.kind.strip() or not self.subject_id.strip():
            raise ValueError("constraint identifiers must not be empty")
        object.__setattr__(self, "value", _freeze_value(self.value))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.constraint_id, "kind": self.kind, "subject_id": self.subject_id, "value": _thaw_value(self.value), "evidence": [item.to_dict() for item in self.evidence]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Constraint":
        return cls(str(value["id"]), str(value["kind"]), str(value["subject_id"]), value.get("value"), tuple(Evidence.from_dict(item) for item in value.get("evidence", ())))


@dataclass(frozen=True)
class ConstraintLedger:
    constraints: tuple[Constraint, ...] = ()
    transformations: tuple[SemanticTransformation, ...] = ()
    rejected_transformation_ids: tuple[str, ...] = ()
    accepted_authorizations: tuple[Authorization, ...] = ()
    version: str = "semantic-constraint-ledger-v1"

    def __post_init__(self) -> None:
        ids = [item.constraint_id for item in self.constraints]
        transformation_ids = [item.transformation_id for item in self.transformations]
        auth_ids = [item.authorization_id for item in self.accepted_authorizations]
        if len(ids) != len(set(ids)) or len(transformation_ids) != len(set(transformation_ids)) or len(auth_ids) != len(set(auth_ids)):
            raise ValueError("ledger ids must be unique")

    def add_constraint(self, constraint: Constraint) -> "ConstraintLedger":
        if any(item.constraint_id == constraint.constraint_id for item in self.constraints):
            raise ValueError(f"duplicate constraint id: {constraint.constraint_id}")
        return replace(self, constraints=self.constraints + (constraint,))

    def authorize(self, authorization: Authorization) -> "ConstraintLedger":
        if any(item.authorization_id == authorization.authorization_id for item in self.accepted_authorizations):
            raise ValueError(f"duplicate authorization id: {authorization.authorization_id}")
        return replace(self, accepted_authorizations=self.accepted_authorizations + (authorization,))

    def attach_authorization(self, transformation_id: str, authorization_id: str) -> "ConstraintLedger":
        """Return a ledger whose presented transformation records its authorization."""
        updated = []
        found = False
        for item in self.transformations:
            if item.transformation_id == transformation_id:
                if item.authorization_id is not None and item.authorization_id != authorization_id:
                    raise ValueError(f"transformation already has another authorization: {transformation_id}")
                updated.append(replace(item, authorization_id=authorization_id))
                found = True
            else:
                updated.append(item)
        if not found:
            raise KeyError(transformation_id)
        return replace(self, transformations=tuple(updated))

    def add_transformation(self, transformation: SemanticTransformation) -> "ConstraintLedger":
        if any(item.transformation_id == transformation.transformation_id for item in self.transformations):
            raise ValueError(f"duplicate transformation id: {transformation.transformation_id}")
        return replace(self, transformations=self.transformations + (transformation,))

    def reject_transformation(self, transformation_id: str) -> "ConstraintLedger":
        if not any(item.transformation_id == transformation_id for item in self.transformations):
            raise KeyError(transformation_id)
        if transformation_id in self.rejected_transformation_ids:
            return self
        return replace(self, rejected_transformation_ids=self.rejected_transformation_ids + (transformation_id,))

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "constraints": [item.to_dict() for item in self.constraints], "transformations": [item.to_dict() for item in self.transformations], "rejected_transformation_ids": list(self.rejected_transformation_ids), "accepted_authorizations": [item.to_dict() for item in self.accepted_authorizations]}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ConstraintLedger":
        return cls(tuple(Constraint.from_dict(item) for item in value.get("constraints", ())), tuple(SemanticTransformation.from_dict(item) for item in value.get("transformations", ())), tuple(str(item) for item in value.get("rejected_transformation_ids", ())), tuple(Authorization.from_dict(item) for item in value.get("accepted_authorizations", ())), str(value.get("version", "semantic-constraint-ledger-v1")))

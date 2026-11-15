"""Parser and semantic evaluator for the v7 pseudo-SQL surface format.

The v7 surface deliberately uses SQL-like text with typed, double-brace
placeholders.  This module treats that text as a private semantic sketch: it
never executes it and never needs the v7 audit sidecar (``pseudo_sql_masks``).
The parser is intentionally dependency-free so it can run before any SQL
planner or Text2SQL model.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping


V7_PLACEHOLDER_CATEGORIES = frozenset({
    "relevant_source", "relevant_field", "relevant_field_set",
    "requested_result", "requested_expression", "contextual_measure",
    "summary_measure", "result_column", "grouping_key", "group_qualification",
    "selection_operator", "selection_expression", "specified_value",
    "ordering", "result_boundary", "distinct_modifier", "query_structure",
    "join_operator", "join_condition", "having_operator", "having_condition",
    "qualification_expression", "nested_query", "related_condition",
    "classification", "computed_expression", "query_component",
    "specified_presence",
})

_PLACEHOLDER = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")
_ANY_BRACES = re.compile(r"\{\{.*?\}\}|\{[^{}]*\}", re.S)
_CLAUSES = (
    "SELECT", "FROM", "JOIN", "WHERE", "GROUP BY", "HAVING",
    "ORDER BY", "LIMIT", "OFFSET", "UNION", "INTERSECT", "EXCEPT", "WITH",
)
_CLAUSE_RE = re.compile(r"\b(?:SELECT|FROM|JOIN|WHERE|GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|OFFSET|UNION|INTERSECT|EXCEPT|WITH)\b", re.I)
_QUALIFIED_FIELD = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")
_TABLE_AFTER = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.I)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"--[^\r\n]*")


@dataclass(frozen=True)
class PseudoSQLPlaceholder:
    token: str
    category: str
    start: int
    end: int


@dataclass(frozen=True)
class PseudoSQLIssue:
    code: str
    message: str
    token: str | None = None


@dataclass(frozen=True)
class PseudoSQLSketch:
    sql: str
    placeholders: tuple[PseudoSQLPlaceholder, ...]
    clause_presence: tuple[str, ...]
    unresolved: tuple[str, ...] = ()
    top_level_clauses: tuple[str, ...] = ()

    @property
    def placeholder_categories(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.category for item in self.placeholders))

    @property
    def placeholder_tokens(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.token for item in self.placeholders))


@dataclass(frozen=True)
class PseudoSQLParseResult:
    sketch: PseudoSQLSketch | None
    issues: tuple[PseudoSQLIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return self.sketch is not None and not self.issues


@dataclass(frozen=True)
class PseudoSQLEvaluation:
    parse: PseudoSQLParseResult
    errors: tuple[PseudoSQLIssue, ...] = ()
    warnings: tuple[PseudoSQLIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return self.parse.valid and not self.errors

    @property
    def all_issues(self) -> tuple[PseudoSQLIssue, ...]:
        return self.parse.issues + self.errors + self.warnings


def _strip_unresolved(text: str) -> tuple[str, tuple[str, ...], list[PseudoSQLIssue]]:
    """Remove an optional v7-compatible audit line from model output."""
    issues: list[PseudoSQLIssue] = []
    unresolved: tuple[str, ...] = ()
    lines = str(text or "").strip().splitlines()
    kept: list[str] = []
    for line in lines:
        if line.strip().casefold().startswith("unresolved:"):
            if unresolved:
                issues.append(PseudoSQLIssue("duplicate_unresolved", "multiple UNRESOLVED lines"))
            raw = line.split(":", 1)[1].strip()
            unresolved = () if raw in {"", "-"} else tuple(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))
        else:
            kept.append(line)
    return "\n".join(kept).strip(), unresolved, issues


def _balanced_parentheses(text: str) -> bool:
    depth = 0
    quote: str | None = None
    escaped = False
    for char in text:
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and quote is None


def _clause_presence(text: str, *, top_level_only: bool = False) -> tuple[str, ...]:
    found: list[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote and (i == 0 or text[i - 1] != "\\"):
                quote = None
            i += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            i += 1
            continue
        if char == "(":
            depth += 1
            i += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        match = _CLAUSE_RE.match(text, i)
        if match and (not top_level_only or depth == 0):
            value = re.sub(r"\s+", " ", match.group(0)).upper()
            if value not in found:
                found.append(value)
            i = match.end()
            continue
        i += 1
    return tuple(found)


def parse_pseudo_sql(text: str, *, schema: Mapping[str, Any] | None = None) -> PseudoSQLParseResult:
    """Parse v7 pseudoSQL without treating it as executable SQL."""
    sql, unresolved, issues = _strip_unresolved(text)
    # Existing v7 surfaces contain occasional audit comments in otherwise
    # valid pseudoSQL. They carry no query semantics, so discard them before
    # clause/token analysis rather than rejecting a compatible surface.
    sql = _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", sql)).strip()
    if not sql:
        issues.append(PseudoSQLIssue("empty_sketch", "pseudoSQL sketch is empty"))
        return PseudoSQLParseResult(None, tuple(issues))
    if ";" in sql.rstrip(";"):
        issues.append(PseudoSQLIssue("multiple_statements", "pseudoSQL must contain one statement"))
    if not _balanced_parentheses(sql):
        issues.append(PseudoSQLIssue("unbalanced_parentheses", "pseudoSQL parentheses or quotes are unbalanced"))
    placeholders: list[PseudoSQLPlaceholder] = []
    for match in _PLACEHOLDER.finditer(sql):
        category = match.group(1)
        token = match.group(0)
        placeholders.append(PseudoSQLPlaceholder(token, category, match.start(), match.end()))
        if category not in V7_PLACEHOLDER_CATEGORIES:
            issues.append(PseudoSQLIssue("unknown_placeholder", f"unsupported v7 placeholder category: {category}", token))
    # Any brace expression that is not an exact v7 token is malformed.  This
    # catches nested placeholders and the model's alternate @{...} grammar.
    for match in _ANY_BRACES.finditer(sql):
        if not _PLACEHOLDER.fullmatch(match.group(0)):
            issues.append(PseudoSQLIssue("malformed_placeholder", "placeholder must use an exact {{name}} token", match.group(0)))
    if not _clause_presence(sql):
        issues.append(PseudoSQLIssue("missing_clause", "pseudoSQL contains no recognized SQL clause"))
    if not re.search(r"\bSELECT\b", sql, re.I):
        issues.append(PseudoSQLIssue("missing_select", "pseudoSQL must preserve SELECT structure"))
    # Qualified references are only checked when a schema is supplied. CTEs,
    # aliases, and placeholders are intentionally ignored here.
    if schema:
        raw_tables = schema.get("tables", schema) if isinstance(schema, Mapping) else {}
        tables = raw_tables if isinstance(raw_tables, Mapping) else {}
        for table, column in _QUALIFIED_FIELD.findall(sql):
            if table.casefold() in {str(item).casefold() for item in tables}:
                actual = next((item for item in tables if str(item).casefold() == table.casefold()), table)
                columns = tables.get(actual, ())
                if isinstance(columns, (list, tuple, set)) and column not in columns:
                    issues.append(PseudoSQLIssue("unknown_schema_field", f"field is absent from supplied schema: {table}.{column}", f"{table}.{column}"))
        known = {str(item).casefold() for item in tables}
        for table in _TABLE_AFTER.findall(sql):
            if not table.startswith("{{") and table.casefold() not in known and table.casefold() not in {"select", "with"}:
                # A derived alias may be introduced by a CTE; avoid rejecting
                # it when its name is visibly declared before AS.
                if not re.search(rf"\bAS\s+{re.escape(table)}\b", sql, re.I):
                    issues.append(PseudoSQLIssue("unknown_schema_source", f"source is absent from supplied schema: {table}", table))
    top = _clause_presence(sql, top_level_only=True)
    sketch = PseudoSQLSketch(sql, tuple(placeholders), _clause_presence(sql), unresolved, top)
    return PseudoSQLParseResult(sketch, tuple(dict.fromkeys(issues)))


def _has_category(sketch: PseudoSQLSketch, categories: Iterable[str]) -> bool:
    wanted = set(categories)
    return any(item.category in wanted for item in sketch.placeholders)


def evaluate_pseudo_sql(request: str, sketch: PseudoSQLSketch | PseudoSQLParseResult | str, *, schema: Mapping[str, Any] | None = None) -> PseudoSQLEvaluation:
    """Evaluate structural/semantic coverage against the natural request."""
    parsed = sketch if isinstance(sketch, PseudoSQLParseResult) else parse_pseudo_sql(sketch, schema=schema) if isinstance(sketch, str) else PseudoSQLParseResult(sketch)
    if parsed.sketch is None:
        return PseudoSQLEvaluation(parsed, parsed.issues)
    item = parsed.sketch
    text = str(request or "").casefold()
    sql = item.sql
    errors: list[PseudoSQLIssue] = []
    warnings: list[PseudoSQLIssue] = []
    categories = set(item.placeholder_categories)
    has_measure = bool(re.search(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", sql, re.I)) or bool(categories & {"requested_result", "requested_expression", "contextual_measure", "summary_measure"})
    ranking = bool(re.search(r"\b(most|highest|lowest|least|top|largest|smallest)\b", text))
    if ranking and "ORDER BY" not in item.clause_presence and "ordering" not in categories:
        errors.append(PseudoSQLIssue("missing_ranking_order", "ranking language requires ORDER BY or {{ordering}}"))
    if re.search(r"\b(top|most common|most frequent)\b", text) and "LIMIT" not in item.clause_presence and "result_boundary" not in categories:
        errors.append(PseudoSQLIssue("missing_ranking_limit", "top/most language requires LIMIT or {{result_boundary}}"))
    if re.search(r"\b(average|avg|mean)\b", text) and not re.search(r"\bAVG\s*\(", sql, re.I) and not ("contextual_measure" in categories or "requested_expression" in categories or "requested_result" in categories or "computed_expression" in categories):
        errors.append(PseudoSQLIssue("missing_average", "average language requires AVG or a typed measure placeholder"))
    if re.search(r"\b(unique|distinct)\b", text) and not re.search(r"\bDISTINCT\b", sql, re.I) and "distinct_modifier" not in categories:
        errors.append(PseudoSQLIssue("missing_distinct", "unique/distinct language requires DISTINCT or {{distinct_modifier}}"))
    if re.search(r"\b(per|each)\b", text):
        has_grain = "GROUP BY" in item.clause_presence or len(re.findall(r"\bSELECT\b", sql, re.I)) > 1 or "grouping_key" in categories
        if not has_grain:
            errors.append(PseudoSQLIssue("missing_grain", "per/each language requires grouping or a nested query"))
    if re.search(r"\b(according to|subject to|relevant criteria|where|whose|paying|in \d{4}|with)\b", text) and "WHERE" not in item.clause_presence and not ("selection_operator" in categories or "selection_expression" in categories):
        warnings.append(PseudoSQLIssue("unrepresented_filter", "request suggests a filter but the sketch has no WHERE or selection placeholder"))
    if not has_measure and re.search(r"\b(count|number|amount|average|total|statistics|occurrences|coverage)\b", text):
        warnings.append(PseudoSQLIssue("weak_measure_evidence", "quantity language has no aggregate or measure placeholder"))
    unresolved_tokens = {token for token in item.placeholder_tokens}
    declared = set(item.unresolved)
    if declared and not declared <= unresolved_tokens and declared != {"-"}:
        warnings.append(PseudoSQLIssue("unresolved_mismatch", "UNRESOLVED lists values that are not placeholder tokens"))
    return PseudoSQLEvaluation(parsed, tuple(errors), tuple(warnings))


__all__ = [
    "V7_PLACEHOLDER_CATEGORIES", "PseudoSQLPlaceholder", "PseudoSQLIssue",
    "PseudoSQLSketch", "PseudoSQLParseResult", "PseudoSQLEvaluation",
    "parse_pseudo_sql", "evaluate_pseudo_sql",
]

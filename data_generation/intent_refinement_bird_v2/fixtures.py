"""Stage-0 BIRD fixture loading and public/private boundary helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .contracts import EvaluationCase, PublicCase, _copy_schema


def normalize_policy(
    policy: Mapping[str, Iterable[str]] | None,
    schema: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    """Normalize role access to existing, deduplicated schema columns.

    Unknown tables/columns are discarded at the boundary.  This keeps later
    permission checks from treating a typo in a role file as a real resource.
    Table and column spelling is preserved, while duplicate entries are removed
    deterministically.
    """
    tables = schema.get("tables", schema)
    if not isinstance(tables, Mapping):
        raise TypeError("schema.tables must be a mapping")
    requested = policy or {}
    result: dict[str, tuple[str, ...]] = {}
    for table, columns in tables.items():
        allowed = set(str(column) for column in requested.get(table, ()))
        result[str(table)] = tuple(str(column) for column in columns if str(column) in allowed)
    return result


def extract_foreign_keys(db_path: str | Path) -> tuple[dict[str, str], ...]:
    """Read declared SQLite foreign keys using ``PRAGMA foreign_key_list``."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    result: list[dict[str, str]] = []
    with sqlite3.connect(path) as connection:
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (table,) in sorted(table_rows, key=lambda row: str(row[0])):
            escaped = str(table).replace('"', '""')
            rows = connection.execute(f'PRAGMA foreign_key_list("{escaped}")').fetchall()
            # SQLite columns: id, seq, table, from, to, on_update, on_delete, match, ...
            for row in sorted(rows, key=lambda item: (int(item[0]), int(item[1]))):
                result.append(
                    {
                        "from_table": str(table),
                        "from_column": str(row[3]),
                        "to_table": str(row[2]),
                        "to_column": str(row[4]),
                    }
                )
    return tuple(result)


def extract_sqlite_schema(db_path: str | Path) -> dict[str, Any]:
    """Read complete SQLite table columns and declared foreign keys."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables: dict[str, list[str]] = {}
        for (table,) in sorted(rows, key=lambda row: str(row[0])):
            escaped = str(table).replace('"', '""')
            columns = connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
            tables[str(table)] = [str(item[1]) for item in columns]
    return {"tables": tables, "foreign_keys": [dict(item) for item in extract_foreign_keys(path)]}


def _row_schema(row: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = row.get("evaluation_only") or {}
    schema = evaluation.get("schema") or row.get("schema") or {}
    return _copy_schema(schema)


def build_public_case(
    row: Mapping[str, Any],
    *,
    role: str = "bird_reader",
    policy: Mapping[str, Iterable[str]] | None = None,
    db_path: str | Path | None = None,
) -> PublicCase:
    """Build the public portion of a BIRD row without private labels."""
    schema = _row_schema(row)
    declared_fks = schema.get("foreign_keys")
    foreign_keys = tuple(dict(item) for item in (declared_fks or ()))
    if not foreign_keys and db_path is not None:
        foreign_keys = extract_foreign_keys(db_path)
    schema["foreign_keys"] = [dict(item) for item in foreign_keys]
    evaluation = row.get("evaluation_only") or {}
    initial_request = str(row.get("natural_language") or row.get("request") or "")
    case_id = str(row.get("source_id") or row.get("case_id") or row.get("db_id") or "bird")
    public_metadata = {
        "db_id": str(row["db_id"]) if row.get("db_id") is not None else None,
        "level": row.get("level"),
        "source_kind": row.get("source_kind"),
    }
    # Never copy the evaluation_only object into public metadata.
    public_metadata = {key: value for key, value in public_metadata.items() if value is not None}
    effective_policy = policy or evaluation.get("policy")
    if effective_policy is None:
        effective_policy = {table: columns for table, columns in schema["tables"].items()}
    return PublicCase(
        case_id=case_id,
        initial_request=initial_request,
        schema=schema,
        foreign_keys=foreign_keys,
        role=str(role),
        policy=normalize_policy(effective_policy, schema),
        metadata=public_metadata,
    )


def build_evaluation_case(
    row: Mapping[str, Any],
    *,
    role: str = "bird_reader",
    policy: Mapping[str, Iterable[str]] | None = None,
    db_path: str | Path | None = None,
    profile: str | None = None,
) -> EvaluationCase:
    """Build a simulator/measurement case while keeping private data local."""
    public = build_public_case(row, role=role, policy=policy, db_path=db_path)
    evaluation = row.get("evaluation_only") or {}
    original = evaluation.get("original_request")
    if original is None:
        originals = evaluation.get("original_questions") or ()
        original = originals[0] if originals else row.get("original_request", "")
    return EvaluationCase(
        public=public,
        original_request=str(original or ""),
        gold_sql=str(evaluation.get("gold_sql") or row.get("gold_sql") or ""),
        pseudo_sql=str(evaluation.get("pseudo_sql") or row.get("pseudo_sql") or ""),
        profile=profile,
        private_metadata={
            key: value
            for key, value in evaluation.items()
            if key not in {"schema", "original_request", "original_questions", "gold_sql", "pseudo_sql"}
        },
    )


def public_agent_payload(case: PublicCase) -> dict[str, Any]:
    return case.agent_payload()


def load_jsonl_cases(
    path: str | Path,
    *,
    role: str = "bird_reader",
    policy_provider: Callable[[Mapping[str, Any]], Mapping[str, Iterable[str]] | None] | None = None,
    db_root: str | Path | None = None,
    limit: int | None = None,
    unique_case: bool = True,
) -> list[PublicCase]:
    """Load public cases from JSONL without exposing evaluation labels."""
    output: list[PublicCase] = []
    seen: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        db_path = None
        if db_root and row.get("db_id"):
            db_path = Path(db_root) / str(row["db_id"]) / f"{row['db_id']}.sqlite"
        case = build_public_case(row, role=role, policy=policy_provider(row) if policy_provider else None, db_path=db_path)
        if unique_case and case.case_id in seen:
            continue
        seen.add(case.case_id)
        output.append(case)
        if limit is not None and len(output) >= limit:
            break
    return output

"""Offline comparison of candidate and witness SQL execution results."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


@dataclass(frozen=True)
class ResultComparison:
    equivalent: bool
    candidate: QueryResult | None
    witness: QueryResult | None
    reason: str


def execute_query(connection: sqlite3.Connection, sql: str) -> QueryResult:
    cursor = connection.execute(str(sql))
    columns = tuple(item[0] for item in cursor.description or ())
    rows = tuple(tuple(row) for row in cursor.fetchall())
    return QueryResult(columns, rows)


def _canonical_row(row: tuple[Any, ...]) -> tuple[Any, ...]:
    def normalize(value: Any) -> Any:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if isinstance(value, float):
            return round(value, 9)
        return value
    return tuple(normalize(value) for value in row)


def compare_results(candidate: QueryResult, witness: QueryResult, *, ordered: bool = False) -> ResultComparison:
    if tuple(column.casefold() for column in candidate.columns) != tuple(column.casefold() for column in witness.columns):
        return ResultComparison(False, candidate, witness, "result_columns_differ")
    candidate_rows = tuple(_canonical_row(row) for row in candidate.rows)
    witness_rows = tuple(_canonical_row(row) for row in witness.rows)
    if ordered:
        equal = candidate_rows == witness_rows
    else:
        equal = Counter(candidate_rows) == Counter(witness_rows)
    return ResultComparison(equal, candidate, witness, "equal" if equal else "result_rows_differ")


def compare_sql_results(connection: sqlite3.Connection, candidate_sql: str, witness_sql: str, *, ordered: bool = False) -> ResultComparison:
    try:
        candidate = execute_query(connection, candidate_sql)
    except sqlite3.Error:
        return ResultComparison(False, None, None, "candidate_execution_error")
    try:
        witness = execute_query(connection, witness_sql)
    except sqlite3.Error:
        return ResultComparison(False, candidate, None, "witness_execution_error")
    return compare_results(candidate, witness, ordered=ordered)

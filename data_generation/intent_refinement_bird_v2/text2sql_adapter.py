"""Narrow Text2SQL boundary for already-authorized final requests.

Permission decisions happen before this adapter.  The model receives the
complete public schema so it can reason about joins and relationships; the SQL
validator remains the enforcement boundary for any field it actually emits.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Protocol


class Text2SQLClient(Protocol):
    def complete(self, *, system_prompt: str, user_payload: dict[str, Any], response_schema: dict[str, Any] | None = None) -> Any:
        ...


class Text2SQLPromptClient(Protocol):
    def complete_text(self, *, system_prompt: str, prompt: str) -> Any:
        ...


TEXT2SQL_SYSTEM_PROMPT = (
    "Generate one executable SQL query for the supplied natural-language "
    "request using only the supplied schema and declared foreign keys. "
    "Permission checking has already been completed before this call. "
    "Return only the SQL command; do not explain it and do not refuse a request."
)


TEXT2SQL_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"sql": {"type": "string"}}, "required": ["sql"],
}


class Text2SQLAdapter:
    def __init__(self, client: Text2SQLClient | Text2SQLPromptClient, *, prompt_mode: str = "repository"):
        self.client = client
        if prompt_mode not in {"repository", "structured"}:
            raise ValueError("prompt_mode must be repository or structured")
        self.prompt_mode = prompt_mode

    @staticmethod
    def _schema_text(public_context: Mapping[str, Any]) -> str:
        supplied = public_context.get("schema_text") or public_context.get("text2sql_schema")
        if isinstance(supplied, str) and supplied.strip():
            return supplied.strip()
        schema = public_context.get("schema", public_context)
        raw_tables = schema.get("tables", {}) if isinstance(schema, Mapping) else {}
        tables = {}
        for table, columns in raw_tables.items() if isinstance(raw_tables, Mapping) else ():
            if not isinstance(columns, (list, tuple, set)):
                continue
            tables[table] = list(columns)
        database = public_context.get("database") or public_context.get("db_id") or "database"
        lines = [f"Database: {database}", f"Total Tables: {len(tables)}", "", "Table Schemas:", ""]
        for table, columns in tables.items() if isinstance(tables, Mapping) else ():
            if not isinstance(columns, (list, tuple, set)):
                continue
            lines.extend([f"Table: {table} ({len(columns)} columns)", "-" * 29])
            for column in columns:
                lines.append(f"• {column}")
            lines.append("")
        return "\n".join(lines).strip()

    @staticmethod
    def _policy_text(public_context: Mapping[str, Any]) -> str:
        role = str(public_context.get("role") or "current_role")
        policy = public_context.get("policy", {})
        lines = [f"Role: {role}", "Accessible Columns:"]
        for table, columns in policy.items() if isinstance(policy, Mapping) else ():
            if isinstance(columns, (list, tuple, set)):
                lines.append(f"  - {table}: {', '.join(str(column) for column in columns)}")
        return "\n".join(lines)

    def build_prompt(self, request: str, public_context: Mapping[str, Any]) -> str:
        """Build the plain-text interface used by the repository Text2SQL models."""
        evidence = public_context.get("evidence")
        schema_text = self._schema_text(public_context)
        raw_schema = public_context.get("schema", public_context)
        raw_fks = public_context.get("foreign_keys", ())
        if not raw_fks and isinstance(raw_schema, Mapping):
            raw_fks = raw_schema.get("foreign_keys", ())
        fk_lines = []
        for item in raw_fks if isinstance(raw_fks, (list, tuple)) else ():
            if isinstance(item, Mapping) and all(key in item for key in ("from_table", "from_column", "to_table", "to_column")):
                fk_lines.append(f"{item['from_table']}.{item['from_column']} -> {item['to_table']}.{item['to_column']}")
        foreign_key_text = "\n".join(fk_lines) if fk_lines else "N/A"
        if self.prompt_mode == "structured":
            parts = [TEXT2SQL_SYSTEM_PROMPT, "", "## Database Schema", schema_text, "", "## Foreign Keys", foreign_key_text, "", "## Question", str(request), "", "## Response"]
        else:
            parts = [TEXT2SQL_SYSTEM_PROMPT, "", "##Instruction:", schema_text, "", "##Foreign Keys:", foreign_key_text, "", "##Evidence:", str(evidence or "N/A"), "", "Generate SQL for the request using this schema and these foreign keys.", "###Input:", str(request), "", "###Response:"]
        return "\n".join(parts)

    def build_payload(self, request: str, public_context: Mapping[str, Any]) -> dict[str, Any]:
        raw_schema = public_context.get("schema", public_context)
        raw_tables = raw_schema.get("tables", {}) if isinstance(raw_schema, Mapping) else {}
        tables = {
            str(table): [str(column) for column in columns]
            for table, columns in raw_tables.items()
            if isinstance(columns, (list, tuple, set))
        }
        raw_fks = public_context.get("foreign_keys", ())
        if not raw_fks and isinstance(raw_schema, Mapping):
            raw_fks = raw_schema.get("foreign_keys", ())
        return {
            "final_request": str(request),
            "schema": {"tables": tables},
            "foreign_keys": [
                {key: str(item[key]) for key in ("from_table", "from_column", "to_table", "to_column") if key in item}
                for item in raw_fks if isinstance(item, Mapping)
            ],
        }

    def generate(self, request: str, public_context: Mapping[str, Any]) -> str:
        prompt = self.build_prompt(request, public_context)
        if hasattr(self.client, "complete_text"):
            raw = self.client.complete_text(system_prompt=TEXT2SQL_SYSTEM_PROMPT, prompt=prompt)
        else:
            # Compatibility path for existing JSON test doubles and generic
            # clients. The production repository adapter should implement
            # complete_text so the model receives the exact plain-text prompt.
            try:
                raw = self.client.complete(system_prompt=TEXT2SQL_SYSTEM_PROMPT, user_payload=prompt, response_schema=None)
            except TypeError:
                raw = self.client.complete(system_prompt=TEXT2SQL_SYSTEM_PROMPT, user_payload=prompt)
        if isinstance(raw, str):
            return self._normalize_sql(raw)
        if isinstance(raw, Mapping) and set(raw) == {"sql"} and isinstance(raw.get("sql"), str) and raw["sql"].strip():
            return self._normalize_sql(raw["sql"])
        raise ValueError("Text2SQL client must return SQL text or a non-empty sql property")

    @staticmethod
    def _normalize_sql(value: str) -> str:
        """Remove presentation wrappers while preserving the SQL itself."""
        text = str(value).strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        if text.lower().startswith("sql:"):
            text = text[4:].strip()
        return text

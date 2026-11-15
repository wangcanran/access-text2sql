"""Small OpenAI-compatible client for the stage-2 parser.

Configuration is read from environment variables, optionally loading the
package-local ``.env``. Values are never included in exceptions or logs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load_dotenv(path: str | Path | None = None) -> None:
    path = Path(path) if path else Path(__file__).with_name(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


class OpenAIJSONClient:
    def __init__(self, *, env_path: str | Path | None = None, model: str | None = None, timeout: float = 90.0, max_json_retries: int = 1):
        load_dotenv(env_path)
        self.model = model or os.getenv("OPENAI_MODEL")
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL") or None
        self.timeout = timeout
        if max_json_retries < 0 or max_json_retries > 2:
            raise ValueError("max_json_retries must be between 0 and 2")
        self.max_json_retries = max_json_retries
        if not self.model:
            raise ValueError("OPENAI_MODEL is not configured")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is not configured")

    def complete(self, *, system_prompt: str, user_payload: dict[str, Any], response_schema: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai package is required for the live parser client") from exc
        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }
        if response_schema is not None:
            request_kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "semantic_parser_response", "strict": True, "schema": response_schema},
            }
        else:
            request_kwargs["response_format"] = {"type": "json_object"}
        last_error: Exception | None = None
        for attempt in range(self.max_json_retries + 1):
            current = dict(request_kwargs)
            if attempt:
                current["messages"] = [
                    *request_kwargs["messages"],
                    {"role": "system", "content": "The previous response was invalid. Return only one valid JSON object matching the requested schema. No markdown, comments, or trailing text."},
                ]
            try:
                try:
                    response = client.chat.completions.create(**current)
                except Exception as exc:
                    message = str(exc).casefold()
                    schema_unsupported = response_schema is not None and any(token in message for token in ("response_format", "json_schema", "structured output", "unsupported"))
                    if not schema_unsupported:
                        raise
                    current["response_format"] = {"type": "json_object"}
                    response = client.chat.completions.create(**current)
                content = response.choices[0].message.content
                value = _decode_json_object(content)
                return value
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                continue
        raise ValueError("parser returned invalid JSON after retries") from last_error


def _decode_json_object(content: Any) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("parser returned empty content")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        # Some OpenAI-compatible gateways append a short explanation.  Only
        # accept a JSON object when the entire response is a JSON value; never
        # salvage arbitrary fragments that could change the contract.
        raise ValueError("parser returned invalid JSON")
    if not isinstance(value, dict):
        raise ValueError("parser response must be a JSON object")
    return dict(value)


class OpenAITextClient:
    """OpenAI-compatible plain-text client for the Text2SQL boundary.

    The client intentionally exposes only the generated text.  It does not
    know about evaluation-only fields, permissions, or SQL validation; those
    remain responsibilities of ``Text2SQLAdapter`` and ``Stage7Runner``.
    """

    def __init__(self, *, env_path: str | Path | None = None, model: str | None = None, timeout: float = 90.0):
        load_dotenv(env_path)
        self.model = model or os.getenv("OPENAI_TEXT2SQL_MODEL") or os.getenv("OPENAI_MODEL")
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL") or None
        self.timeout = timeout
        if not self.model:
            raise ValueError("OPENAI_MODEL or OPENAI_TEXT2SQL_MODEL is not configured")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is not configured")

    def complete_text(self, *, system_prompt: str, prompt: str) -> str:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai package is required for the live Text2SQL client") from exc
        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
        response = client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ValueError("Text2SQL model returned empty content")
        return content.strip()

"""Safe adapter for the contract parser migration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contract_parser import CONTRACT_PARSER_PROMPT, CONTRACT_REPAIR_NOTE, CONTRACT_RESPONSE_SCHEMA, ContractParseRequest, ContractParseResult, ContractParserClient, parse_contract_response
from .parse_errors import ParseAttempt, ParseFailure
from .contract_binding import resolve_contract_bindings


@dataclass(frozen=True)
class ContractParseOutcome:
    result: ContractParseResult | None = None
    failure: ParseFailure | None = None
    attempts: tuple[ParseAttempt, ...] = ()


class ContractParseAdapter:
    def __init__(self, client: ContractParserClient, *, max_repair_attempts: int = 1):
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be non-negative")
        self.client = client
        self.max_repair_attempts = max_repair_attempts

    def parse_safe(self, request: ContractParseRequest) -> ContractParseOutcome:
        attempts: list[ParseAttempt] = []
        payload = request.to_payload()
        previous_error: str | None = None
        for attempt_index in range(self.max_repair_attempts + 1):
            prompt = CONTRACT_PARSER_PROMPT
            if attempt_index:
                prompt += "\n" + CONTRACT_REPAIR_NOTE
                if previous_error:
                    prompt += f"\nThe exact validation error was: {previous_error}\nFix that error and return the complete object again. Do not output an explanation."
            try:
                raw = self.client.complete(system_prompt=prompt, user_payload=payload, response_schema=CONTRACT_RESPONSE_SCHEMA)
                parsed = parse_contract_response(request.request, raw)
                resolved = resolve_contract_bindings(parsed.contract, request.public_context)
                parsed = type(parsed)(resolved, parsed.coverage)
                attempts.append(ParseAttempt(parsed, None))
                return ContractParseOutcome(parsed, None, tuple(attempts))
            except Exception as exc:
                previous_error = str(exc)
                attempts.append(ParseAttempt(None, ParseFailure("contract_parse_attempt_failed", str(exc), attempt_index < self.max_repair_attempts)))
                if attempt_index >= self.max_repair_attempts:
                    return ContractParseOutcome(None, ParseFailure("contract_parse_failed", str(exc), attempt_index), tuple(attempts))
        return ContractParseOutcome(None, ParseFailure("contract_parse_failed", "no parse attempt succeeded", self.max_repair_attempts), tuple(attempts))

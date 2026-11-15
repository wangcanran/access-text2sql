"""Render an authorized intent contract into a compact business request."""

from __future__ import annotations

from .intent_contract import IntentContract, MeasureSpec, OutputSpec
from .user_renderer import UserRenderer


def _measure_text(measure: MeasureSpec, renderer: UserRenderer) -> str:
    text = measure.meaning
    if measure.inner_measure:
        text += f" (average of {measure.inner_measure.meaning}"
        if measure.scope_id:
            text += f" at scope {measure.scope_id}"
        text += ")"
    elif measure.operation.upper() not in {"COUNT", "SUM"}:
        text = f"{measure.operation.upper()} of {text}"
    if measure.distinct and "distinct" not in text.casefold() and "unique" not in text.casefold():
        text = "distinct " + text
    return renderer.business(text)


def _output_text(output: OutputSpec, renderer: UserRenderer) -> str:
    if output.measure:
        detail = _measure_text(output.measure, renderer)
        text = output.meaning if output.meaning.casefold() in detail.casefold() else f"{output.meaning} ({detail})"
    elif output.field_refs and output.meaning.casefold().find("label") >= 0:
        text = output.meaning
    elif output.field_refs:
        labels = [renderer.binding(item) for item in output.field_refs]
        text = " and ".join(labels)
    else:
        text = output.meaning
    if output.distinct and "distinct" not in text.casefold() and "unique" not in text.casefold():
        text = "distinct " + text
    return renderer.business(text)


def render_contract_request(contract: IntentContract, *, renderer: UserRenderer | None = None) -> str:
    renderer = renderer or UserRenderer()
    request = f"Provide {_output_text(contract.outputs[0], renderer) if contract.outputs else 'the requested information'} for {renderer.business(contract.subject.meaning)}"
    if len(contract.outputs) > 1:
        request = f"Provide {', '.join(_output_text(item, renderer) for item in contract.outputs)} for {renderer.business(contract.subject.meaning)}"
    if contract.groupings:
        request += "; grouped by " + ", ".join(renderer.business(item.meaning) for item in contract.groupings)
    if contract.ranking:
        request += f"; select the {renderer.business(contract.ranking.meaning)}"
        if contract.ranking.limit:
            request += f" (top {contract.ranking.limit})"
    for item in contract.filters:
        condition = renderer.business(item.meaning)
        if item.value is not None:
            condition += f" ({item.operator or 'matches'} {item.value})"
        request += "; filter by " + condition
    for item in contract.constraints:
        request += "; " + renderer.business(item.meaning)
    return renderer.business(request).strip().rstrip(".") + "."

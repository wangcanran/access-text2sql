"""Add precise diagnostics to rejected coarse JSON; keep validators authoritative.

This optional recovery adapter never edits a response, a request, or an accepted
state. It only appends details to ValueError feedback for a model's next retry.
"""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch

from scripts.audit_ablation_coarse_failures import explain


def _rejected(exc, raw, request, *, choices_removed=False):
    diagnostic = deepcopy(raw)
    if choices_removed and isinstance(diagnostic, dict):
        for row in diagnostic.get('fragments', []):
            if isinstance(row, dict):
                row.setdefault('open_choices', [])
    details = explain(diagnostic, request)
    suffix = '; '.join(details)
    return ValueError(str(exc) + ('; exact contract violations: ' + suffix if suffix else ''))


@contextmanager
def installed(module):
    original_choices = module.source_choices
    original_fragments = module.validate_fragments

    def choices(raw, key):
        try:
            return original_choices(raw, key)
        except ValueError as exc:
            if key != 'fragments' or not isinstance(raw, dict):
                raise
            raise _rejected(exc, raw, raw.get('original_text', '')) from exc

    def fragments(raw, request):
        try:
            return original_fragments(raw, request)
        except ValueError as exc:
            raise _rejected(exc, raw, request, choices_removed=True) from exc

    with patch.object(module, 'source_choices', choices), patch.object(module, 'validate_fragments', fragments):
        yield

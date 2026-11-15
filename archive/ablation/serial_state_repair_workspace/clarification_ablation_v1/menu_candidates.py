"""Preserve business alternatives sharing a physical column in frozen v10.

The frozen compiler's column-keyed normalization is intentionally not edited.
Its candidate resolver and compiled-decision hook bracket that normalization:
retain the validated alternatives there, then pass them through the original
typed compiler and unchanged menu authorization/navigation/answer machinery.
"""
from copy import deepcopy

from .answer_protocol import MenuAnswerProtocolMethod

VERSION = 'main_v10_menu_candidates_fixed_v1'


def normalize_candidates(rows, context):
    canonical = {(t['name'] + '.' + c['name']).casefold(): t['name'] + '.' + c['name']
                 for t in context['tables'] for c in t['columns']}
    if not isinstance(rows, list):
        raise ValueError('Candidates must be a list')
    seen, result = set(), []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('column'), str)
                or row['column'].casefold() not in canonical
                or not isinstance(row.get('label'), str) or not row['label'].strip()):
            raise ValueError('Candidates need schema columns and business labels')
        column, label = canonical[row['column'].casefold()], row['label'].strip()
        identity = (column, label.casefold())
        if identity not in seen:
            seen.add(identity)
            result.append(dict(column=column, label=label))
    return result


class FixedMenuMethod(MenuAnswerProtocolMethod):
    """Original menus and state machine, corrected candidate identity only."""

    def compile_decision(self, call, payload, context, *, field, scope=False):
        previous = getattr(self, '_candidate_identity_frame', None)
        self._candidate_identity_frame = {}
        try:
            return super().compile_decision(call, payload, context, field=field, scope=scope)
        finally:
            self._candidate_identity_frame = previous

    def _resolve_candidates(self, raw):
        resolved = super()._resolve_candidates(raw)
        frame = getattr(self, '_candidate_identity_frame', None)
        if frame is not None:
            # Called afresh for every response/retry, after handle validation.
            frame['rows'] = deepcopy(resolved['field_candidates'])
        return resolved

    def compiled_decision(self, decision, source, context):
        frame = getattr(self, '_candidate_identity_frame', None)
        if frame is not None and 'rows' in frame:
            decision = deepcopy(decision)
            rows = frame['rows']
            if decision['scope_status'] == 'fixed' and not rows:
                # Keep the original compiler's established-candidate fallback.
                rows = [c for c in source.get('field_candidates', [])
                        if c['column'] in decision['required_columns']]
            decision['field_candidates'] = normalize_candidates(rows, context)
        # In particular, operation/value reclassification still removes menus.
        return super().compiled_decision(decision, source, context)

    def audit(self):
        return dict(super().audit(), version=VERSION, variant='menu_candidates_fixed_v1',
                    candidate_identity='canonical_column_and_business_label',
                    menu_mechanism_unchanged=True, all_questions_open=False)

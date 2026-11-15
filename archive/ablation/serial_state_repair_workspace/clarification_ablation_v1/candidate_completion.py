"""Add schema-grounded alternatives without replacing existing menu choices."""
from copy import deepcopy

from .attribute_candidates import CandidateGenerationUnresolved, generate_candidates
from .candidate_context import SchemaAlignedMenuMethod

VERSION = 'main_v10_menu_candidates_completion_v3'


def append_candidates(original, additions):
    """Preserve original order and identity; append only validated alternatives."""
    result = deepcopy(original)
    seen = {(c['column'], c['label'].strip().casefold()) for c in result}
    for candidate in additions:
        identity = (candidate['column'], candidate['label'].strip().casefold())
        if identity not in seen:
            seen.add(identity)
            result.append(deepcopy(candidate))
    return result


class CandidateCompletionMenuMethod(SchemaAlignedMenuMethod):
    """An opt-in additive pass over documented schema attributes, before gates."""

    def prepare_menu(self, call, session, plan, context, max_options=5):
        previous = getattr(self, '_completion_public_context', None)
        self._completion_public_context = dict(
            public_request=session.get('original_request', ''),
            public_conversation=[dict(question=deepcopy(t['question']),
                answer={k:deepcopy(v) for k,v in t['answer'].items()
                        if k in {'action','text','choices'}})
                for t in session.get('public_interactions', [])])
        try:
            return super().prepare_menu(call, session, plan, context, max_options)
        finally:
            self._completion_public_context = previous

    def decision_input(self, prompt, payload):
        prompt, payload = super().decision_input(prompt, payload)
        if payload.get('decision_type') == 'field_decision':
            payload.update(deepcopy(getattr(self, '_completion_public_context', None) or {}))
        return prompt, payload

    def compile_decision(self, call, payload, context, *, field, scope=False):
        def completed_call(stage, prompt, data):
            raw = call(stage, prompt, data)
            if (stage.removesuffix('_contract_retry') != 'field_decision'
                    or not isinstance(raw, dict)
                    or raw.get('scope_status') != 'fixed'
                    or raw.get('clarification', {}).get('dimension') != 'field'
                    or not isinstance(raw.get('field_candidates'), list)):
                return raw
            # Contract retries wrap the original public input.
            public = data.get('input', data) if 'schema' not in data else data
            handles = public.get('candidate_catalog', {})
            original = raw['field_candidates']
            if any(not isinstance(c, dict) or c.get('column') not in handles
                   or not isinstance(c.get('label'), str) or not c['label'].strip()
                   for c in original):
                return raw  # Let the existing compiler report its contract error.
            if not original:
                # Preserve the established-candidate fallback that a nonempty
                # supplementary response would otherwise bypass downstream.
                reverse = {column: handle for handle,column in handles.items()}
                original = [dict(column=reverse[c['column']],label=c['label'])
                            for c in payload['source'].get('field_candidates', [])
                            if c['column'] in raw.get('required_columns', [])
                            and c['column'] in reverse]
            event = dict(source_id=public.get('source', {}).get('id'),
                         focus=public.get('question_focus'), original_count=len(original))
            try:
                generated = generate_candidates(call, public)
                additions = [dict(column=c['attribute']['column_handle'], label=c['label'])
                             for c in generated['candidates']]
                result = deepcopy(raw)
                result['field_candidates'] = append_candidates(original, additions)
                event.update(status='completed', added_count=len(result['field_candidates'])-len(original),
                             attributes=[dict(attribute_id=c['attribute_id'],
                                 column=c['attribute']['column'], json_path=c['attribute']['json_path'],
                                 label=c['label']) for c in generated['candidates']])
            except (CandidateGenerationUnresolved, ValueError, TypeError, KeyError) as error:
                # An empty/invalid completion never authorizes deletion and
                # never destroys the original compiler response.
                event.update(status='fallback_to_original', added_count=0,
                             error=type(error).__name__+': '+str(error))
                result = raw
            if not hasattr(self, '_candidate_completion_events'):
                self._candidate_completion_events = []
            self._candidate_completion_events.append(event)
            return result
        return super().compile_decision(completed_call, payload, context, field=field, scope=scope)

    def audit(self):
        return dict(super().audit(), version=VERSION, variant='menu_candidates_completion_v3',
                    candidate_completion='schema_attribute_select_review_additive',
                    original_candidates_preserved=True,
                    candidate_completion_events=deepcopy(getattr(self, '_candidate_completion_events', [])))

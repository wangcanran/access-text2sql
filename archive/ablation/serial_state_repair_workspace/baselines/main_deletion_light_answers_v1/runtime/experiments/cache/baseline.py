"""Main deletion with the existing lightweight path for ordinary answers.

This is an explicit combination, not a claim that past hybrid deletion passed.
Deletion reconstruction, refinement and evidence reconciliation are main's.
"""
from ...method import MainMethod
from ..decision_update.lightweight import LightweightUpdate
from ..decision_update.hybrid import RENDER

VERSION = 'main_deletion_light_answers_v1'


class LightMainMethod(LightweightUpdate):
    # No code deletion, survivor snapshots or hybrid withdrawal in this baseline.
    _remove = MainMethod._remove

    def compile_decision(self, call, payload, context, *, field, scope=False):
        # A blocked contribution feeds main's removal contract, including its
        # literal source/remainder provenance. Keep that compiler unchanged.
        if not field and not scope:
            return MainMethod.compile_decision(self, call, payload, context, field=field, scope=scope)
        return super().compile_decision(call, payload, context, field=field, scope=scope)

    def analyze_session(self, call, session, context, reconstruct=None):
        if session.get('progressive_deletion_base'):
            return MainMethod.analyze_session(self, call, session, context, reconstruct)
        return super().analyze_session(call, session, context, reconstruct)

    def analyze_flat(self, call, payload, context):
        if self._event and self._event.get('kind') == 'removal':
            return MainMethod.analyze_flat(self, call, payload, context)
        return super().analyze_flat(call, payload, context)

    # Reuse the tested role-explicit final renderer, not hybrid's removal path.
    def render_session(self, call, session):
        def role_explicit(stage, prompt, payload):
            if stage.removesuffix('_contract_retry') == 'final_active_request':
                payload = dict(payload)
                active = dict(payload['input'] if stage.endswith('_contract_retry') else payload)
                nodes = active.pop('nodes')
                active['returned_values'] = [n for n in nodes if n['role'] == 'output']
                active['nonreturned_requirements'] = [n for n in nodes if n['role'] != 'output']
                if stage.endswith('_contract_retry'):
                    payload['input'] = active
                else:
                    payload = active
                prompt = RENDER
            return call(stage, prompt, payload)
        return super().render_session(role_explicit, session)

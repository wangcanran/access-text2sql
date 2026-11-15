"""Opt-in deletion from a bound question, without a compiled remainder."""
from copy import deepcopy

from .hybrid import HybridUpdate
from .lightweight import node_payload

VERSION = 'direct_local_removal_v1'

REMOVE = '''Withdraw only event.purpose, the local purpose of the actual question.
Derive what survives from editable_nodes, not from a proposed remainder or past
intent. readonly_nodes already exist independently: do not copy their demands
into edited nodes. They provide context and surviving attachments only.

Return JSON: {"updates":{"f1":[{"id":"p1","text":"surviving requirement",
"role":"output","columns":["t.c"],"questions":{"field":"Which information?"}}]},
"edges":[["p1","f2","applies_to"]]}.
Omit unchanged parents; [] deletes the whole parent. Only editable_ids may change.
Changed children require id, text and ALL surviving schema dependencies in columns.
Split children require roles: output is a returned value; modifier is a condition,
subject association or result instruction. Do not turn filter operands into outputs.
kind/tables inherit their parent unless supplied. questions lists ALL unresolved
choices for each changed child; omit only when none remain. Use exact table.column
identifiers. Do not report pending IDs or retire_columns; code computes retirement.

Preserve independent contributions inside a compound parent and preserve_outputs.
Withdraw the local purpose's own parameters with it. Review shared modifiers:
retain their surviving attachments and meaning; remove only qualifications that
exist solely for the withdrawn purpose. An independent filter or subject link
does not disappear merely because a nearby output is removed.
Keep surviving subjects, associations, calculations, AND/OR, negation and ambiguity.
If a surviving subject is uncertain, retain a scope question. A surviving condition
still needs its columns, even if restricted: withdrawal is not authorization to
erase dependencies while keeping their meaning. Do not invent replacement intent.

Edges: applies_to means source qualifies target; context means source depends on
target. Return changed/split attachments, including surviving readonly endpoints.
Code preserves unchanged and same-role one-to-one edges. No SQL, explanations,
access workarounds, historical restoration or extra outputs.
'''


def without_remainder(decision):
    return {k: deepcopy(v) for k, v in decision.items() if k != 'remaining_requirements'}


class DirectRemovalUpdate(HybridUpdate):
    def decision_input(self, prompt, payload):
        # Keep the menu compiler identical except for removing the redundant
        # survivor-generation task. Missing remainder is not an empty remainder.
        prompt = prompt.replace(
            ',\n"remaining_requirements":["independent remainder inside this source"]}', '}')
        start = prompt.index('remaining_requirements describes only contributions')
        end = prompt.index('Keep one/several/all ambiguity.', start)
        return prompt[:start] + prompt[end:], payload

    def compiled_decision(self, decision, source, context):
        return without_remainder(decision)

    def _transaction(self, call, session, base, event, context):
        if event['kind'] != 'removal':
            return super()._transaction(call, session, base, event, context)
        event = deepcopy(event)
        event['decision'] = without_remainder(event.get('decision', {}))

        def direct_call(stage, prompt, payload):
            payload = deepcopy(payload)
            retry = stage.endswith('_contract_retry')
            active = payload['input'] if retry else payload
            editable = set(active['editable_ids'])
            active.pop('nodes')
            active['editable_nodes'] = [node_payload(n) for i, n in base['items'].items()
                                        if i in editable]
            active['readonly_nodes'] = [node_payload(n) for i, n in base['items'].items()
                                        if i not in editable]
            active['event']['purpose'] = without_remainder(active['event']['purpose'])
            active['event']['purpose']['required_columns'] = event['decision'].get('required_columns', [])
            return call(stage.replace('deletion_snapshot', 'direct_removal'), REMOVE, payload)

        return super()._transaction(direct_call, session, base, event, context)

    def _remove(self, call, session, basis, q, trigger, context):
        q = deepcopy(q)
        q['scoped_permission_decision'] = without_remainder(q.get('scoped_permission_decision', {}))
        # Without a whole-purpose certificate, do not infer a code fast path.
        result = super()._remove(call, session, basis, q, trigger, context)
        result['transaction_audit'][-1]['execution'] = VERSION + ':model'
        result['reduction_audit'][-1].update(method=VERSION, analysis_mode='direct_local_removal')
        return result

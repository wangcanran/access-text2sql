"""Explain existing contract errors on retries without editing model answers.

No Gold SQL, labels, grants or reference results enter this adapter. Original
validators remain the sole authority; only contract_error text is augmented.
"""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch

VERSION = 'contract_error_details_v3'


def details(error, raw):
    if not isinstance(raw, dict):
        return ''
    if 'Rewrite these exact physical schema spellings' in error:
        return ('Changing letter case is not a paraphrase. Rewrite the flagged physical identifiers '
                'as business language everywhere, including quoted context, while preserving the '
                'question, choices, meaning and existing response contract.')
    if 'Selected fields must survive in the answered purpose' in error:
        return ('Read input.event.selected, the confirmed option list. ALL selected options are '
                'simultaneous user choices, not alternatives for you to choose among. '
                'Every selected option dependency '
                'must occur in required_columns of a replacement child of the answered target with '
                'the appropriate purpose role. Keeping it only in an unrelated node or a modifier '
                'does not preserve a returned-field choice. Preserve the confirmed meaning, claim '
                'lineage and other required dependencies; do not invent a different user answer.')
    if 'declared output but only carries non-returned contributions' in error:
        return ('Preserve each inherited obligation use. Do not turn a condition, operand or subject '
                'into a returned claim. A non-output contribution must also have a path through '
                'affects to an actual returned output; do not fix its semantic_class while leaving '
                'it disconnected. affects names consumers, not providers. Follow the existing '
                'obligation_ids and request to identify those consumers; do not invent outputs.')
    if 'A bound claim must name its real schema dependencies' in error:
        bad = []
        for row in raw.get('items', raw.get('fragments', [])):
            if not isinstance(row, dict):
                continue
            for claim in row.get('binding_claims', []):
                if isinstance(claim, dict) and claim.get('state') == 'bound' and not claim.get('columns') and claim.get('use') in ('returned', 'subject'):
                    bad.append(dict(item=row.get('item_id', row.get('id')), meaning=claim.get('meaning'), use=claim.get('use')))
        return ('Inspect empty bound claims ' + repr(bad) +
                '. A bound returned value or subject identity needs real schema dependencies; '
                'literal operands and conditions may be column-free. Use the existing open-claim '
                'protocol where the requested binding is genuinely unknown; do not invent fields.') if bad else ''
    if 'An output needs a returned claim' in error:
        bad = []
        for row in raw.get('items', raw.get('fragments', [])):
            if not isinstance(row, dict) or row.get('role') != 'output':
                continue
            claims = row.get('binding_claims', [])
            if claims and all(isinstance(c, dict) and c.get('use') in ('subject', 'condition', 'operand') for c in claims):
                bad.append(row.get('item_id', row.get('id', row.get('text', '<unnamed>'))))
        return ('Inspect output contributions ' + repr(bad) +
                '. An output must describe an actually requested returned value with a returned claim; '
                'a subject locator or filter alone is not a returned value. Correct the declaration '
                'using the existing role/claim contract and the request; do not invent outputs or drop conditions.') if bad else ''
    if 'Every modifier must contribute to a returned output' in error:
        nodes = raw.get('nodes', [])
        if not isinstance(nodes, list) or any(not isinstance(n, dict) for n in nodes):
            return ''
        reachable = {n.get('id') for n in nodes if n.get('semantic_class') == 'output'}
        while True:
            expanded = reachable | {n.get('id') for n in nodes if isinstance(n.get('affects'), list) and any(a in reachable for a in n['affects'])}
            if expanded == reachable:
                break
            reachable = expanded
        bad = [n.get('id') for n in nodes if n.get('id') not in reachable]
        return ('Contributions without a path through affects to any output: ' + repr(bad) +
                '. affects lists consumers of this contribution, not its dependencies. '
                'Preserve requested conditions and dependencies, use only existing aliases, '
                'and declare their actual consumers under the existing semantic_class contract.') if bad else ''
    return ''


def augment(stage, payload):
    if not stage.endswith('_contract_retry') or not isinstance(payload, dict):
        return payload
    diagnostic = details(str(payload.get('contract_error', '')), payload.get('previous_response'))
    if not diagnostic:
        return payload
    result = deepcopy(payload)
    if 'Selected fields must survive in the answered purpose' in str(payload.get('contract_error', '')):
        event = (payload.get('input') or {}).get('event', {})
        selected = event.get('selected', [])
        dependencies = sorted({c for option in selected if isinstance(option, dict)
                               for c in option.get('required_columns', []) if isinstance(c, str)})
        diagnostic += (' Confirmed target=' + repr(event.get('target')) +
                       '; purpose=' + repr(event.get('purpose')) +
                       '; all confirmed selected dependencies=' + repr(dependencies) + '.')
    result['contract_error'] += '; specific contract diagnostics: ' + diagnostic
    return result


@contextmanager
def installed(recorder_class):
    original = recorder_class.bind

    def bind(self, *args, **kwargs):
        call = original(self, *args, **kwargs)

        def invoke(stage, prompt, payload):
            return call(stage, prompt, augment(stage, payload))
        return invoke

    with patch.object(recorder_class, 'bind', bind):
        yield

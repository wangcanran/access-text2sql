"""Bounded semantic review before publishing a local partition."""
from ...engine.analysis import validated_call
from .state import require, text

PROMPT = '''Review a proposed local refinement, not its permissions or SQL feasibility.
The parent is authoritative surviving content. The proposal cannot delete intent.
Return one JSON object exactly {"accepted":true,"findings":[]} or
{"accepted":false,"findings":[{"kind":"missing|duplicate|scope_changed|not_independent",
"detail":"specific mismatch with parent and affected part aliases"}]}.
accepted is a boolean; findings is an array. Each finding has exactly kind and detail
as nonempty strings. kind must be ONE of the four listed values. accepted must equal
whether findings is empty. No extra keys, Markdown, replacement plan, or schema fields.
Check: every requested result and condition survives; conditions do not replace
the requested output; parts do not duplicate the same result; conditions are not
broadened or lost. Names and corresponding values can be one coherent result.
Shared subject wording is not duplicate output. A compound remainder may preserve
unspecified details without enumerating them. Splitting is justified by independent
business results, not by nouns/columns alone. Do not demand a full decomposition,
new preferences, or executable bindings. Review single-part rewrites too.
'''


def review(call, parent, plan):
    def checked(raw):
        require(isinstance(raw, dict) and set(raw) == {'accepted', 'findings'}
                and type(raw['accepted']) is bool and isinstance(raw['findings'], list), 'Invalid semantic review')
        for f in raw['findings']:
            require(isinstance(f, dict) and set(f) == {'kind', 'detail'}
                    and f['kind'] in ('missing', 'duplicate', 'scope_changed', 'not_independent')
                    and text(f['detail']), 'Invalid semantic finding')
        require(raw['accepted'] == (not raw['findings']), 'Review verdict conflicts with findings')
        return raw
    return validated_call(call, 'tree_partition_review', PROMPT,
                          dict(parent=parent, parts=plan['parts'], relations=plan['relations']), checked)


def prepare_reviewed(call, state, target, context, prompt, payload, prepare, audits):
    """One semantic repair, then stop with original tree intact if still rejected."""
    request = payload
    for attempt in range(2):
        plan = validated_call(call, 'tree_focus' if not attempt else 'tree_focus_semantic_retry',
                              prompt, request, lambda raw: prepare(state, target, raw, context))
        verdict = review(call, payload['editable_leaf'], plan)
        audits.append(dict(target=target, revision=state['revision'], plan=plan, review=verdict))
        if verdict['accepted']:
            return plan
        request = dict(input=payload, rejected_plan=plan, semantic_findings=verdict['findings'],
                       instruction='Return a complete corrected focus plan. Preserve parent intent; prefer no split when one result suffices.')
    return None

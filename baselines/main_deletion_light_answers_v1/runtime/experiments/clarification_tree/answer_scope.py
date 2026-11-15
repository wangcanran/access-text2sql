"""Answer-only cancellation review; never inject global cancellations into planning."""
from copy import deepcopy
from ...engine.analysis import validated_call
from .state import require, text

PROMPT = '''Review only the CURRENT USER ANSWER for exact semantic repetitions of
outputs already removed for permission reasons. Return JSON
{"repeated_ids":[], "evidence":{}, "remaining_answer":"..."}.
Match the complete requested output and its subject/scope, not shared words or
categories. A cancelled balance range does NOT cancel transaction history.
Uncertain matches must be retained. evidence maps each repeated ID to an exact
quote from answer.text or selected_labels. Remove only those repeated requests
from remaining_answer; preserve all novel requests, exclusions and conditions.
If no match, return remaining_answer equal to answer.text. Empty remaining_answer
is allowed only when the entire answer consists of repeated cancelled requests.
This review cannot cancel any current node or change permission policy.
Return exactly these three keys, no Markdown or extra fields. repeated_ids is an
array of unique STRING IDs from permission_cancelled_outputs keys, not objects.
evidence is an object whose keys equal repeated_ids exactly and values are nonempty
verbatim quote strings. remaining_answer is a string, never null or a list.
No-match response: {"repeated_ids":[],"evidence":{},"remaining_answer":"the exact original answer.text"}.
The last value is illustrative: copy the actual input answer.text unchanged.
'''


def review(call, state, event):
    cancelled = {}
    for entry in state['audit']:
        if entry.get('action') == 'focused_commit' and entry.get('event', {}).get('reason') == 'no_authorized_local_plan':
            iid = entry['focus_id']
            cancelled[iid] = state['nodes'][iid]['meaning']
        if entry.get('action') == 'explicit_cancel' and entry.get('automatic'):
            iid = entry['target']
            cancelled[iid] = state['nodes'][iid]['meaning']
    if not cancelled:
        return event, None
    answer = event.get('answer', {})
    if not text(answer.get('text')):
        return event, None
    def checked(raw):
        require(isinstance(raw, dict) and set(raw) == {'repeated_ids', 'evidence', 'remaining_answer'}, 'Invalid answer scope review')
        ids = raw['repeated_ids']
        require(isinstance(ids, list) and all(isinstance(i, str) for i in ids)
                and len(ids) == len(set(ids)) and set(ids) <= set(cancelled), 'Invalid repeated IDs')
        require(isinstance(raw['evidence'], dict) and set(raw['evidence']) == set(ids), 'Missing repeat evidence')
        sources = [answer['text']] + event.get('selected_labels', [])
        require(all(text(v) and any(v in s for s in sources) for v in raw['evidence'].values()), 'Repeat evidence must quote current answer')
        require(isinstance(raw['remaining_answer'], str), 'Invalid remaining answer')
        require(bool(ids) or raw['remaining_answer'] == answer['text'], 'No-match review cannot rewrite answer')
        return raw
    result = validated_call(call, 'tree_answer_scope', PROMPT,
                            dict(answer=answer, selected_labels=event.get('selected_labels', []),
                                 permission_cancelled_outputs=cancelled), checked)
    filtered = deepcopy(event)
    filtered['answer']['text'] = result['remaining_answer']
    return filtered, result

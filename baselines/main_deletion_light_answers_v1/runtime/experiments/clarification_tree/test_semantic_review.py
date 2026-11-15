from copy import deepcopy
from .semantic_review import prepare_reviewed
from .contribution import prepare
from .focus_context import payload
from .state import initial
from .test_tree import C


def test_rejected_partition_never_mutates_tree_and_can_be_repaired():
    state = initial('Return school information with a threshold condition')
    before = deepcopy(state)
    bad = dict(focus=dict(meaning='Threshold condition', granularity='atomic', role='modifier', columns=[]),
               remainders=[], question=dict(dimension='value', question='Which threshold?'), coverage='Condition')
    good = deepcopy(bad)
    good['remainders'] = [dict(meaning='Return school information', granularity='compound')]
    audits = []
    def call(stage, prompt, data):
        assert state == before
        if stage == 'tree_focus':
            return bad
        if stage == 'tree_focus_semantic_retry':
            assert data['semantic_findings'][0]['kind'] == 'missing'
            return good
        assert stage == 'tree_partition_review'
        return dict(accepted=bool(data['parts'][1:]), findings=[] if len(data['parts']) > 1 else
                    [dict(kind='missing', detail='Requested school information output is absent')])
    plan = prepare_reviewed(call, state, 'f1', C, '', payload(state, 'f1', C), prepare, audits)
    assert len(plan['parts']) == 2 and len(audits) == 2
    assert state == before


def test_repeated_semantic_failure_is_bounded_and_preserves_parent():
    state = initial('Attribute names and corresponding values')
    before = deepcopy(state)
    raw = dict(focus=dict(meaning='Attributes and values', granularity='atomic', role='output', columns=[]),
               remainders=[dict(meaning='Same attributes and values', granularity='compound')],
               question=dict(dimension='field', question='Which attributes?'), coverage='Both')
    stages = []
    def call(stage, prompt, data):
        stages.append(stage)
        if stage == 'tree_partition_review':
            return dict(accepted=False, findings=[dict(kind='duplicate', detail='Both parts return the same attribute/value pairs')])
        return raw
    assert prepare_reviewed(call, state, 'f1', C, '', payload(state, 'f1', C), prepare, []) is None
    assert len(stages) == 4 and state == before

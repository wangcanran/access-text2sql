"""Method-specific decision adapters; Gold scorer and metric math unchanged."""
def classify(arm,front,score,category):
    upstream_refusal=front['outcome']=='refuse'
    pruning=bool(front.get('removed_goals'))
    assert pruning==bool(front.get('has_pruning'))
    downstream_refusal=bool(score and score['generation_status']=='downstream_refusal')
    if arm=='serial':
        assert not pruning and not upstream_refusal
        denied=downstream_refusal
        decision_source='sql_backend_refusal'
    elif arm=='joint':
        denied=upstream_refusal or pruning
        decision_source='frontend_refusal_or_explicit_removal'
    else:raise ValueError(arm)
    match=bool(score and score['original_execution_match'])
    declared=front.get('complete_request_decision','unknown')
    return dict(front,category=category(front['original_permission']=='allowed',denied,match),
        original_execution_match=match,predicted_complete_request_denied=denied,
        decision_source=decision_source,backend_refusal=downstream_refusal,
        frontend_declared_decision=declared,
        frontend_decision_behavior_mismatch=(arm=='joint' and declared!=('denied' if denied else 'allowed')),
        sql_access=score['sql_access'] if score else 'no_sql',
        sql_executed=bool(score and score['sql_executed']),
        generation_status=score['generation_status'] if score else 'upstream_refusal',
        generation_error=bool(score and score['generation_status'] in ('generation_error','empty')))

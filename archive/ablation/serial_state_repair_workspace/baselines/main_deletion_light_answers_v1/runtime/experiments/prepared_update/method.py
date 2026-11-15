"""Prepare semantic templates before asking; execute menu answers in code."""
from copy import deepcopy

from ..menu_partition.method import MenuPartition
from ..cancel_answer.method import cancelling, material_event
from ..decision_update.lightweight import transaction, node_payload

VERSION = 'light_prepared_update_v1'
PREPARE = '''
QUESTION INTENT: inspect query_overview before locating this local question.
query_overview is READ-ONLY context. Only source is being decomposed. Other
existing nodes are NOT remainders of source: never clone them into partition,
route source pending IDs to them, or reconstruct them as new local children.
Refer to existing neighbor IDs in edges when the source attachment must survive.
If source already is a dedicated local purpose, use partition:null even when
other query_overview nodes still have unanswered questions. Global awareness
means preserving those existing nodes, not incorporating them into this question.
Additionally return update_intent:{"purpose":"what uncertainty this question
resolves", "on_accept":"how an answer changes the requirement and related
constraints", "on_cancel":"what local purpose is withdrawn; what survives",
"preserve":["independent unanswered requirements that must remain"],
"update_nodes":["node_id"]}.
update_nodes is the exact set of existing node IDs that the later updater may
change after this answer or cancellation. Use the IDs in query_overview. Include
the asked node and only nodes whose meaning/attachment can change because of
this answer. If a node must be split first, list its current ID; partition it
now, then include only the child IDs that this answer can affect (use local IDs
such as ask/rest for nodes created by this partition). Never include all graph
nodes as a precaution. Nodes omitted from this list are mechanically read-only.
These are conditional instructions, NOT an assumed answer. Identify the relation
between the selected purpose, its subject, calculations and shared restrictions.
Do not ask a narrow field question intending to resolve quantity or a formula.
The updater will receive this intent together with the actual user answer.
PREPARED UPDATE: additionally return binding:null, or binding:{
"template":"Return {fields} for the requested records",
"common_columns":["table.join_key"]} for a fixed FIELD menu.
The single literal {fields} is filled by code with the chosen business labels.
Preserve the subject, predicate, quantity/coverage and calculation in the template;
replace ONLY this menu's field choice. Never invent a formula, scope or output.
common_columns are established dependencies of THIS local purpose independent
of which candidate is chosen (including required joins/subject restrictions).
Do not include candidate alternatives as common dependencies. Keep denied common
dependencies too: lack of access does not authorize silently changing the subject.
Use binding:null for a business-scope choice, a complete semantic alternative,
or an operation that cannot be expressed by this field substitution.
Partition EVERY independent remainder before the menu. A null partition certifies
that cancelling this purpose cancels the entire node, not just part of it.
For a non-null partition, also return dependency_coverage:{"old.column":["node_id"]}.
Every old dependency of the source must occur exactly in this map and be assigned
to one or more returned nodes. The listed node IDs must exist. Do not omit join,
subject, date, or permission dependencies merely because they are not displayed.
Questions in partition nodes use field for unknown business data, scope for
unknown business entity, operation for computation, value ONLY for literal values
or quantity coverage. Route existing pending IDs; do not duplicate their questions
under another dimension. The earlier questions example is illustrative, not a
requirement to use value. Do not infer an unspecified financial ratio formula.
'''


class PreparedUpdate(MenuPartition):
    def __init__(self, *, execute_prepared=False):
        super().__init__()
        # Default experiment isolates question-intent handoff; deterministic
        # execution is a separate variable, not evidence for that hypothesis.
        self.execute_prepared = execute_prepared

    def decision_input(self, prompt, payload):
        prompt, payload = super().decision_input(prompt, payload)
        base = self._partition_session['view']
        payload['query_overview'] = {
            'editable_source_id': payload['source']['id'],
            'other_nodes_read_only': True,
            'nodes': [node_payload(n) for n in base['items'].values()],
            'edges': [[e[k] for k in ('source_id', 'target_id', 'kind')]
                      for e in base['relations']['edges']]}
        instructions = PREPARE
        if not self.execute_prepared:
            instructions = (PREPARE[:PREPARE.index('PREPARED UPDATE:')]
                            + PREPARE[PREPARE.index('Partition EVERY'):])
        return prompt + instructions, payload

    def compiled_decision(self, decision, source, context):
        intent = decision.get('update_intent')
        if (not isinstance(intent, dict)
                or any(not isinstance(intent.get(k), str) or not intent[k].strip()
                       for k in ('purpose', 'on_accept', 'on_cancel'))
                or not isinstance(intent.get('preserve'), list)
                or any(not isinstance(s, str) for s in intent['preserve'])):
            raise ValueError('Question needs conditional update intent and preserved requirements')
        update_nodes = intent.get('update_nodes')
        if not isinstance(update_nodes, list) or not update_nodes or any(
                not isinstance(i, str) or not i for i in update_nodes):
            raise ValueError('Question needs explicit update_nodes')
        if len(set(update_nodes)) != len(update_nodes):
            raise ValueError('update_nodes must contain unique node IDs')
        binding = decision.get('binding')
        if binding is not None:
            columns = {t['name'] + '.' + c['name'] for t in context['tables'] for c in t['columns']}
            if (not isinstance(binding, dict) or set(binding) != {'template', 'common_columns'}
                    or not isinstance(binding['template'], str)
                    or binding['template'].count('{fields}') != 1
                    or not isinstance(binding['common_columns'], list)
                    or any(c not in columns for c in binding['common_columns'])):
                raise ValueError('Binding needs a field template and schema common_columns')
        split = decision.get('partition')
        if split is not None:
            nodes = split.get('nodes') if isinstance(split, dict) else None
            coverage = decision.get('dependency_coverage')
            if not isinstance(nodes, list) or not isinstance(coverage, dict):
                raise ValueError('Partition needs dependency_coverage for every source dependency')
            aliases = {n.get('id') for n in nodes if isinstance(n, dict)}
            source_deps = set(source.get('required_columns', []))
            if set(coverage) != source_deps:
                missing = sorted(source_deps - set(coverage))
                extra = sorted(set(coverage) - source_deps)
                raise ValueError('Dependency coverage mismatch; missing=' + repr(missing) +
                                 ' extra=' + repr(extra))
            for col, owners in coverage.items():
                if (not isinstance(owners, list) or not owners or
                        any(owner not in aliases for owner in owners)):
                    raise ValueError('Dependency coverage has invalid owners for ' + col)
            by_node = {n.get('id'): set(n.get('columns', [])) for n in nodes if isinstance(n, dict)}
            for col, owners in coverage.items():
                # dependency_coverage is the authoritative ownership statement.
                # Models often list a join dependency in the coverage map but
                # omit it from the child projection. Add that explicit
                # dependency mechanically; do not ask the model to repeat the
                # same fact in two fields and retry on formatting drift.
                for owner in owners:
                    node = next(n for n in nodes if n.get('id') == owner)
                    node.setdefault('columns', [])
                    if col not in node['columns']:
                        node['columns'].append(col)
        decision['prepared_atomic'] = True
        split = decision.get('partition')
        aliases = [n.get('id') for n in split.get('nodes', [])] if isinstance(split, dict) else []
        existing = set(self._partition_session['view']['items'])
        valid = set(aliases) | existing | {'ask'}
        invalid = set(update_nodes) - valid
        if invalid:
            raise ValueError('update_nodes references unknown nodes: ' + repr(sorted(invalid)))
        if not ({'ask', source['item_id']} & set(update_nodes)):
            raise ValueError('update_nodes must include the asked node')
        result = super().compiled_decision(decision, source, context)
        committed = result.get('_partition_commit')
        if committed is not None:
            audit = committed['partition_audit'][-1]
            child_map = dict(zip(aliases, audit['children']))
            resolved = [child_map.get(i, source['item_id'] if i == 'ask' else i)
                        for i in update_nodes]
            resolved = list(dict.fromkeys(resolved))
            result['update_node_ids'] = resolved
            committed['partition_decisions'][audit['asked']]['update_node_ids'] = resolved
        else:
            resolved = [source['item_id'] if i == 'ask' else i for i in update_nodes]
            result['update_node_ids'] = list(dict.fromkeys(resolved))
        return result

    def edit_boundary(self, base, event):
        decision = event.get('turn', {}).get('scoped_permission_decision', {})
        declared = decision.get('update_node_ids')
        if decision.get('prepared_atomic') and declared:
            ids = set(declared)
            if event['target'] not in ids or not ids <= set(base['items']):
                raise ValueError('Prepared update_nodes must be current nodes and include the target')
            return ids
        return super().edit_boundary(base, event)

    def _transaction(self, call, session, base, event, context):
        decision = event.get('turn', {}).get('scoped_permission_decision', {})
        if self.execute_prepared and cancelling(event) and decision.get('prepared_atomic'):
            semantic = material_event(event)
            boundary = self.edit_boundary(base, event)
            raw = {'updates': {iid: [] for iid in boundary}}
            result = transaction(session, base, semantic, raw, context,
                                 editable_ids=boundary, keep_shared=True)
            return self._audit(result, 'cancel', event)

        turn = event.get('turn', {})
        selected = turn.get('selected_options', [])
        binding = decision.get('binding')
        if (self.execute_prepared and selected and binding and decision.get('scope_status') == 'fixed'
                and turn.get('selection_mode') in ('return_fields', 'operand')
                and not turn.get('answer', {}).get('text', '').strip()):
            target = event['target']
            owner = base['items'][target]
            # No new dependencies can be smuggled into an accepted menu answer.
            chosen = {c for option in selected for c in option['required_columns']}
            if not chosen <= set(session['allowed']):
                raise ValueError('Prepared selection contains unauthorized dependencies')
            deps = chosen | set(binding['common_columns'])
            text = binding['template'].replace('{fields}', ', '.join(o['text'] for o in selected))
            row = {'id': 'bound', 'text': text, 'columns': sorted(deps),
                   'role': owner['role'], 'kind': owner['kind'],
                   'tables': sorted(set(c.split('.')[0] for c in deps)) or owner['scope_tables'],
                   'questions': {}}
            raw = {'updates': {target: [row]},
                   'retire_columns': {target: sorted(set(owner['required_columns']) - deps)}}
            result = transaction(session, base, event, raw, context, editable_ids={target})
            return self._audit(result, 'bind_fields', event)

        def guided_call(stage, prompt, payload):
            payload = deepcopy(payload)
            active = payload.get('input', payload)
            active['question_update_intent'] = deepcopy(decision.get('update_intent'))
            active['question_scope'] = {
                'editable_target': event['target'],
                'editable_ids': sorted(self.edit_boundary(base, event)),
                'prepared_update_nodes': sorted(self.edit_boundary(base, event)),
                'asked_decision_ids': sorted(event.get('turn', {}).get('decision_ids', [])),
                'read_only_nodes': sorted(set(base['items']) - self.edit_boundary(base, event)),
            }
            prompt += ('\nUse question_update_intent to interpret the purpose of the '
                       'question, not as user evidence. The actual answer controls '
                       'the update. Preserve unrelated unanswered requirements and '
                       'shared restrictions. Intent grants no extra write authority. '
                       'Do not create a new question with a different wording for an '
                       'asked decision ID or its answered dimension.\n')
            return call(stage, prompt, payload)
        result = super()._transaction(guided_call, session, base, event, context)
        return self._audit(result, 'local_semantic_parse', event)

    @staticmethod
    def _audit(result, operation, event):
        result.setdefault('prepared_update_audit', []).append({
            'operation': operation, 'target': event['target'],
            'post_answer_model_calls': 0 if operation != 'local_semantic_parse' else None})
        return result

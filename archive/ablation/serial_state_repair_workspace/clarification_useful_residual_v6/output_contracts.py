"""Model wire formats end here; existing validators own state and semantics.

This is an input boundary, not another graph/state representation. Every model
stage uses the same parse -> normalize -> shape -> state-validation sequence.
The legacy adapters receive an owned, canonical dictionary, never the response
object itself. Only lossless, explicitly declared wire aliases are normalized.
"""
from copy import deepcopy
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import json
import math
import sys
from unittest.mock import patch


VERSION = 'model_output_boundary_v2'


class OutputContractError(ValueError):
    def __init__(self, stage, path, reason):
        self.stage, self.path, self.reason = stage, path, reason
        super().__init__(f'{stage} {path}: {reason}')


def fail(stage, path, reason):
    raise OutputContractError(stage, path, reason)


def object_at(value, stage, path):
    if not isinstance(value, dict):
        fail(stage, path, 'expected an object')
    return value


def strings(value, stage, path):
    if not isinstance(value, list):
        fail(stage, path, 'expected an array of strings')
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            fail(stage, f'{path}[{i}]', 'expected a nonempty string')


def fields(value, required, optional, stage, path, *, exact=False):
    object_at(value, stage, path)
    missing = set(required) - set(value)
    if missing:
        fail(stage, path, 'missing fields: ' + ', '.join(sorted(missing)))
    if exact and set(value) - set(required) - set(optional):
        fail(stage, path, 'unexpected fields: ' + ', '.join(sorted(set(value) - set(required) - set(optional))))
    for key, expected in {**required, **optional}.items():
        if key in value and type(value[key]) is not expected:
            fail(stage, path + '.' + key, 'expected ' + expected.__name__)


def string_map(value, stage, path):
    object_at(value, stage, path)
    for key, item in value.items():
        if not isinstance(item, str) or not item.strip():
            fail(stage, path + '.' + key, 'expected a nonempty string')


def claims_at(claims, stage, path):
    if not isinstance(claims, list):
        fail(stage, path, 'expected an array')
    for i, claim in enumerate(claims):
        here = f'{path}[{i}]'
        fields(claim, {'meaning': str, 'columns': list},
               {'use': str, 'state': str, 'obligation_ids': list}, stage, here)
        strings(claim['columns'], stage, here + '.columns')
        if 'obligation_ids' in claim:
            strings(claim['obligation_ids'], stage, here + '.obligation_ids')


def candidates_at(rows, stage, path):
    if not isinstance(rows, list):
        fail(stage, path, 'expected an array')
    for i, row in enumerate(rows):
        fields(row, {'column': str, 'label': str}, {}, stage, f'{path}[{i}]')


def node_at(row, stage, path):
    fields(row, {'id': str, 'text': str, 'columns': list},
           {'role': str, 'kind': str, 'tables': list, 'questions': dict,
            'candidates': dict, 'pending': list, 'binding_claims': list}, stage, path)
    strings(row['columns'], stage, path + '.columns')
    for key in ('tables', 'pending'):
        if key in row:
            strings(row[key], stage, path + '.' + key)
    for key in ('questions', 'candidates'):
        if key in row:
            string_map(row[key], stage, path + '.' + key)
    if 'binding_claims' in row:
        claims_at(row['binding_claims'], stage, path + '.binding_claims')


def parse_object(raw, stage):
    if isinstance(raw, str):
        def pairs(rows):
            result = {}
            for key, value in rows:
                if key in result:
                    fail(stage, '$', 'duplicate JSON key: ' + key)
                result[key] = value
            return result
        try:
            raw = json.loads(raw, object_pairs_hook=pairs)
        except json.JSONDecodeError as exc:
            fail(stage, '$', 'invalid JSON: ' + str(exc))
    def json_value(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    fail(stage, path, 'JSON object keys must be strings')
                json_value(item, path + '.' + key)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                json_value(item, f'{path}[{i}]')
        elif type(value) not in (str, int, float, bool, type(None)):
            fail(stage, path, 'expected JSON data')
        elif isinstance(value, float) and not math.isfinite(value):
            fail(stage, path, 'non-finite number')
    object_at(raw, stage, '$')
    json_value(raw, '$')
    return deepcopy(raw)


def normalize_wire(value, stage):
    """Remove empty non-decisions and preserve structured unresolved reasons."""
    family = STAGE_FAMILIES.get(stage)
    if family in ('transaction', 'decomposition'):
        nodes = (value.get('nodes', []) if family == 'decomposition' else
                 [child for children in value.get('updates', {}).values()
                  for child in (children if isinstance(children, list) else [children])]
                 if isinstance(value.get('updates'), dict) else [])
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict) or not isinstance(node.get('questions'), dict):
                    continue
                for dimension, question in list(node['questions'].items()):
                    if (isinstance(question, dict) and set(question) == {'question'}
                            and isinstance(question['question'], str)
                            and question['question'].strip()):
                        node['questions'][dimension] = question['question']
    if family == 'analysis' and isinstance(value.get('items'), list):
        if 'binding_claims' in value:
            items = value['items']
            if len(items) != 1 or not isinstance(items[0], dict) or 'binding_claims' in items[0]:
                fail(stage, '$.binding_claims', 'top-level claims have no unique item owner')
            items[0]['binding_claims'] = value.pop('binding_claims')
        for row in value['items']:
            if not isinstance(row, dict) or not isinstance(row.get('question_focus'), dict):
                continue
            missing = row.get('missing')
            if not isinstance(missing, list):
                continue
            focus = row['question_focus']
            for dimension, question in list(focus.items()):
                if (isinstance(dimension, str) and isinstance(question, str)
                        and not question.strip() and dimension not in missing):
                    del focus[dimension]
    if family == 'grounding' and isinstance(value.get('unresolved'), list):
        normalized = []
        for item in value['unresolved']:
            if (isinstance(item, dict) and set(item) == {'claim_id', 'reason'}
                    and all(isinstance(item[key], str) and item[key].strip()
                            for key in ('claim_id', 'reason'))):
                normalized.append(item['claim_id'] + ': ' + item['reason'])
            else:
                normalized.append(item)
        value['unresolved'] = normalized
    return value


def transaction_at(raw, stage):
    fields(raw, {'updates': dict}, {}, stage, '$')
    # Envelope members are reserved, not parent IDs. Preserve values and reject
    # disagreement; never drop a wrongly nested routing/dependency declaration.
    envelope = {'edges': [], 'retire_columns': {}, 'question_changes': {}, 'settled_decisions': []}
    for key, default in envelope.items():
        if key in raw['updates']:
            nested = raw['updates'].pop(key)
            if key in raw and raw[key] != nested:
                fail(stage, '$.' + key, 'conflicting envelope declarations')
            raw[key] = nested
        raw.setdefault(key, deepcopy(default))
    # Historical empty-list retirement represents no retirement, unambiguously.
    if raw['retire_columns'] == []:
        raw['retire_columns'] = {}
    fields(raw, {'updates': dict, 'edges': list, 'retire_columns': dict,
                 'question_changes': dict, 'settled_decisions': list},
           {'followup_routes': dict}, stage, '$', exact=True)
    dense = list(raw['settled_decisions'])
    for parent, children in raw['updates'].items():
        if isinstance(children, dict):
            children = raw['updates'][parent] = [children]
        if not isinstance(children, list):
            fail(stage, '$.updates.' + parent, 'expected a child array')
        for i, child in enumerate(children):
            path = f'$.updates.{parent}[{i}]'
            object_at(child, stage, path)
            if 'settled_decisions' in child:
                strings(child['settled_decisions'], stage, path + '.settled_decisions')
                dense.extend(child.pop('settled_decisions'))
            node_at(child, stage, path)
    strings(dense, stage, '$.settled_decisions')
    raw['settled_decisions'] = list(dict.fromkeys(dense))
    for i, edge in enumerate(raw['edges']):
        strings(edge, stage, f'$.edges[{i}]')
        if len(edge) != 3:
            fail(stage, f'$.edges[{i}]', 'expected [source, target, kind]')
    for parent, cols in raw['retire_columns'].items():
        strings(cols, stage, '$.retire_columns.' + parent)
    for decision, change in raw['question_changes'].items():
        path = '$.question_changes.' + decision
        fields(change, {}, {'resolved': str, 'to': list, 'question': str}, stage, path, exact=True)
        if not change or ('resolved' in change and set(change) != {'resolved'}):
            fail(stage, path, 'expected resolved or to/question, exclusively')
        if 'to' in change:
            strings(change['to'], stage, path + '.to')
        for key in ('resolved', 'question'):
            if key in change and not change[key].strip():
                fail(stage, path + '.' + key, 'expected a nonempty string')


# Stages share structural families; state-specific enums, identities, schema
# membership, authority and semantic meaning remain in their existing validators.
STAGE_FAMILIES = {
    'progressive_coarse': 'coarse', 'progressive_analysis': 'analysis',
    'semantic_transaction': 'transaction', 'obligation_grounding': 'grounding',
    'field_decision': 'decision', 'scope_decision': 'decision', 'permission_decision': 'decision',
    'contract_permission_decomposition': 'decomposition', 'contract_none_decomposition': 'decomposition',
    'answer_contribution_review': 'review', 'deferred_commit_review': 'review',
    'session_user_gold': 'requester', 'public_wording': 'public',
    'hierarchical_menu_groups': 'groups', 'hierarchical_field_labels': 'labels',
    'hierarchical_field_filter': 'field_filter',
    'scope_menu_choices': 'scope_options', 'category_propagation': 'category',
    'final_active_request': 'render', 'binding_completion': 'completion',
    'contract_none_focus': 'focus',
}


def validate_shape(raw, stage):
    family = STAGE_FAMILIES.get(stage)
    if family == 'transaction':
        transaction_at(raw, stage)
    elif family == 'grounding':
        fields(raw, {'resolutions': list, 'unresolved': list}, {}, stage, '$', exact=True)
        strings(raw['unresolved'], stage, '$.unresolved')
        for i, row in enumerate(raw['resolutions']):
            path = f'$.resolutions[{i}]'
            fields(row, {'claim_id': str, 'columns': list}, {}, stage, path, exact=True)
            strings(row['columns'], stage, path + '.columns')
        if raw['resolutions'] and raw['unresolved']:
            fail(stage, '$', 'unresolved binding cannot partially commit')
    elif family in ('analysis', 'category'):
        fields(raw, {'items': list}, {}, stage, '$')
        for i, row in enumerate(raw['items']):
            path = f'$.items[{i}]'
            fields(row, {'item_id': str}, {}, stage, path)
            if family == 'analysis':
                fields(row, {'role': str, 'kind': str, 'requirement': str,
                             'scope_tables': list, 'required_columns': list,
                             'missing': list, 'question_focus': dict}, {}, stage, path)
                for key in ('scope_tables', 'required_columns', 'missing'):
                    strings(row[key], stage, path + '.' + key)
                string_map(row['question_focus'], stage, path + '.question_focus')
                if 'field_candidates' in row:
                    candidates_at(row['field_candidates'], stage, path + '.field_candidates')
                if 'binding_claims' in row:
                    claims_at(row['binding_claims'], stage, path + '.binding_claims')
            else:
                strings(row.get('columns'), stage, path + '.columns')
    elif family == 'decision':
        fields(raw, {'requirement': str, 'role': str, 'kind': str, 'scope_tables': list,
                     'required_columns': list, 'field_candidates': list},
               {'remaining_requirements': list, 'clarification': dict, 'question': str}, stage, '$')
        for key in ('scope_tables', 'required_columns', 'remaining_requirements'):
            if key in raw:
                strings(raw[key], stage, '$.' + key)
        candidates_at(raw['field_candidates'], stage, '$.field_candidates')
        if 'clarification' in raw:
            fields(raw['clarification'], {'dimension': str}, {}, stage, '$.clarification', exact=True)
    elif family == 'decomposition':
        fields(raw, {'nodes': list, 'retired_dependencies': dict}, {}, stage, '$')
        for i, row in enumerate(raw['nodes']):
            path = f'$.nodes[{i}]'
            node_at(row, stage, path)
            fields(row, {'parent': str, 'semantic_class': str, 'affects': list},
                   {'decision_ids': list}, stage, path)
            strings(row['affects'], stage, path + '.affects')
            if 'decision_ids' in row:
                strings(row['decision_ids'], stage, path + '.decision_ids')
        # v10 decomposition uses the same canonical shape as graph.py:
        # physical-column -> nonempty retirement explanation.  The inherited
        # transaction contract used parent -> column-list, which caused valid
        # model plans to be rejected during the retry boundary.
        for column, reason in raw['retired_dependencies'].items():
            if not isinstance(column, str) or not column.strip():
                fail(stage, '$.retired_dependencies', 'keys must be nonempty column names')
            if not isinstance(reason, str) or not reason.strip():
                fail(stage, '$.retired_dependencies.' + column,
                     'expected a nonempty retirement explanation string')
    elif family == 'coarse':
        fields(raw, {'original_text': str, 'fragments': list}, {}, stage, '$')
        for i, row in enumerate(raw['fragments']):
            fields(row, {'item_id': str, 'span': str}, {'open_choices': list}, stage, f'$.fragments[{i}]')
            for j, choice in enumerate(row.get('open_choices', [])):
                fields(choice, {'dimension': str, 'span': str, 'question': str},
                       {'answer_kind': str}, stage, f'$.fragments[{i}].open_choices[{j}]')
    elif family == 'review':
        fields(raw, {'accepted': bool, 'issues': list}, {}, stage, '$', exact=True)
        for i, issue in enumerate(raw['issues']):
            object_at(issue, stage, f'$.issues[{i}]')
    elif family == 'requester':
        fields(raw, {'action': str}, {'module_ids': list, 'selected_ids': list, 'answer': str}, stage, '$')
        for key in ('module_ids', 'selected_ids'):
            if key in raw:
                strings(raw[key], stage, '$.' + key)
    elif family == 'public':
        fields(raw, {'public': dict}, {}, stage, '$')
    elif family in ('groups', 'scope_options'):
        fields(raw, {'options': list}, {}, stage, '$')
        for i, row in enumerate(raw['options']):
            path = f'$.options[{i}]'
            key = 'choice_ids' if family == 'groups' else 'required_columns'
            label = 'label' if family == 'groups' else 'text'
            fields(row, {label: str, key: list}, {}, stage, path)
            strings(row[key], stage, path + '.' + key)
    elif family == 'labels':
        fields(raw, {'options': list}, {}, stage, '$', exact=True)
        for i, row in enumerate(raw['options']):
            fields(row, {'choice_id': str, 'label': str}, {}, stage, f'$.options[{i}]', exact=True)
    elif family == 'field_filter':
        fields(raw, {'columns': list}, {}, stage, '$', exact=True)
        strings(raw['columns'], stage, '$.columns')
    elif family == 'render':
        fields(raw, {'request': str}, {}, stage, '$', exact=True)
    elif family == 'completion':
        fields(raw, {'nodes': list, 'unresolved': list}, {}, stage, '$', exact=True)
        strings(raw['unresolved'], stage, '$.unresolved')
        for i, row in enumerate(raw['nodes']):
            object_at(row, stage, f'$.nodes[{i}]')
    elif family == 'focus':
        fields(raw, {'blocked_aliases': list, 'reason': str}, {}, stage, '$', exact=True)
        strings(raw['blocked_aliases'], stage, '$.blocked_aliases')
    # Other inherited stages still get JSON ownership/type validation plus
    # their mandatory existing validator. No unknown stage bypasses that step.


@dataclass(frozen=True)
class CompiledOutput:
    stage: str
    _value: dict

    def to_legacy(self):
        """Explicit adapter for the existing state machine's dictionary API."""
        return deepcopy(self._value)


def compile_output(stage, raw):
    stage = stage.removesuffix('_contract_retry')
    value = normalize_wire(parse_object(raw, stage), stage)
    validate_shape(value, stage)
    return CompiledOutput(stage, value)


def validated_output_call(call, stage, prompt, payload, validator):
    """Run shape AND state validation inside the same bounded retry boundary.

    Transport exceptions and implementation bugs are not converted to content
    failures. Validation works on an owned copy; the supplied raw response is
    retained untouched in traces and retry diagnostics.
    """
    for attempt in range(2):
        actual_stage = stage if not attempt else stage + '_contract_retry'
        actual_payload = payload if not attempt else {
            'input': payload, 'previous_response': raw, 'contract_error': str(error),
            'instruction': 'Return the complete corrected JSON object following the stated contract.'}
        raw = call(actual_stage, prompt, actual_payload)
        try:
            compiled = compile_output(stage, raw)
            return validator(compiled.to_legacy())
        except (ValueError, TypeError, KeyError) as exc:
            error = exc if isinstance(exc, OutputContractError) else OutputContractError(stage, '$state', str(exc))
            if attempt:
                if error is exc:
                    raise
                raise error from exc


@contextmanager
def installed_output_boundary(analysis_module):
    """Adapt frozen runtime entry points, including imported function aliases.

    Patching analysis.validated_call alone misses modules that used
    `from .analysis import validated_call` (menus, simulation, propagation).
    Match identity inside this runtime package only; restore every alias on
    exit. Higher-level method adapters must be installed AFTER this boundary.
    """
    original = analysis_module.validated_call
    prefix = analysis_module.__name__.split('.')[0] + '.'
    bindings = [(module, name) for module_name, module in list(sys.modules.items())
                if module is not None and module_name.startswith(prefix)
                for name, value in list(vars(module).items()) if value is original]
    with ExitStack() as stack:
        for module, name in bindings:
            stack.enter_context(patch.object(module, name, validated_output_call))
        yield

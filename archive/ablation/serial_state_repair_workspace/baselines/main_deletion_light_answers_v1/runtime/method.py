"""Integrated clarification method: coarse evidence, progressive menus and local revision.

The active session orchestration and its prompts are collected here, rather than
inheriting experimental session adapters. Shared schema, authorization, menu and
trace infrastructure lives in the local engine package; historical
permission evidence uses the shared lineage utility. No private reference or
SQL enters the method. See README.md for the verified boundary.
"""
from collections import Counter
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import re
from unittest.mock import patch

from .model import QueryFrame
from .objective_contract import has_query_objective, validate_removal_objective
from .runtime import engine_module as frozen_module, engine_hashes as frozen_hashes
from .permission_lineage import reconcile, digest, evidence_sources

METHOD_VERSION = 'integrated_main_v1'
CLARITY_VERSION = 'source_choices_v1'

# Reuses the language-only segmentation/refinement call; no extra model stage.
SOURCE_CHOICES = '''
In addition, give every returned fragment/piece an open_choices array (empty
when none). Read only what the language actually specifies. Record a choice if
the requested business scope, attribute, operation or concrete parameter remains
unspecified; do not pick a likely interpretation. Each entry is
{"dimension":"scope|field|operation|value", "span":"exact quote inside this
fragment/piece", "question":"one question about that unresolved choice"}.
This records semantic uncertainty, not SQL implementation alternatives. Do not
demand an unstated tie policy, numeric limit, identifier or formal definition
when the request already conveys a usable intent. Preserve legitimate vagueness
without inventing a more specific intent. No schema or output/modifier roles.
After a real answer or removal, assess the CURRENT wording and the actual event;
answered/deleted choices disappear, but an answer to one part cannot resolve a
different part. A category navigation is not a concrete field selection.
'''

CHOICE_MAPPING = '''
source_open_choices records unresolved choices identified without a schema.
Preserve those missing dimensions and their original questions in each item's
missing/question_focus. A plausible or sole available schema field cannot
resolve them. Put hypothetical fields in field_candidates; required_columns
contains only dependencies already determined by the request/effective answers.
These records describe current editable text, not permanent gaps from an old
request. Resolve them through actual dialogue/refinement, never schema guessing.
'''


def source_choices(raw, key):
    """Validate quoted uncertainty and strip it from the legacy source contract."""
    if not isinstance(raw, dict) or not isinstance(raw.get(key), list):
        raise ValueError('Expected source fragments/pieces array')
    clean = deepcopy(raw)
    choices = []
    for row in clean[key]:
        if not isinstance(row, dict):
            raise ValueError('Expected a source fragment/piece object')
        entries = row.pop('open_choices', None)
        if not isinstance(entries, list):
            raise ValueError('Every source fragment/piece needs open_choices, possibly []')
        seen = set()
        for choice in entries:
            if (not isinstance(choice, dict) or set(choice) != {'dimension', 'span', 'question'}
                    or choice['dimension'] not in ('scope', 'field', 'operation', 'value')
                    or not isinstance(choice['span'], str) or not choice['span'].strip()
                    or not isinstance(row.get('span'), str) or choice['span'] not in row['span']
                    or not isinstance(choice['question'], str) or not choice['question'].strip()):
                raise ValueError('Open choices need a supported dimension, exact local quote and question')
            # The scheduler has one slot per dimension per node; multiple
            # subquestions may be combined in that slot, preserving their text.
            identity = (choice['dimension'], choice['span'], choice['question'])
            if identity in seen:
                raise ValueError('Duplicate source choice')
            seen.add(identity)
        choices.append(entries)
    return clean, choices


def preserve_source_choices(raw, choices):
    """Keep source-level open decisions when schema mapping reports them clear."""
    raw = deepcopy(raw)
    if not isinstance(raw, dict) or not isinstance(raw.get('items'), list):
        return raw  # The normal contract supplies the diagnostic.
    for row in raw['items']:
        if not isinstance(row, dict):
            continue
        entries = choices.get(row.get('item_id'), [])
        if not entries:
            continue
        if not isinstance(row.get('missing'), list) or not isinstance(row.get('question_focus'), dict):
            raise ValueError('Mapping needs missing array and question_focus object')
        for dim in dict.fromkeys(e['dimension'] for e in entries):
            if dim not in row['missing']:
                row['missing'].append(dim)
            row['question_focus'][dim] = ' '.join(dict.fromkeys(
                e['question'] for e in entries if e['dimension'] == dim))
    return raw



# Collected semantic_mapping.py (active implementation only).
MAPPING_RULES = '''
Distinguish discourse context from database operations using the existing types.
A pure reference to an already stated population, or a heading introducing listed
outputs, is an instruction when it adds no attribute, calculation, predicate or
join. If that reference is clear, use grounded=true, missing=[], and no invented
column dependency. Do not attach a primary key just to represent "these records".
A real cross-table relationship still needs its actual join dependencies; a vague
or unresolved referent still needs a scope question.

Preserve what is requested before choosing a schema implementation. An entity is
not its count, identifier or another attribute. A stored count is still a count;
its availability does not authorize replacing requested entities with a number.
If the returned attribute/measure is unspecified, keep the field choice open;
if the operation is unspecified, keep operation open. Candidate meanings must
match the stated entity, not a related entity reachable through a join.
Declare each contribution's determined operands and required join keys, including
explicit time comparisons even when another choice remains open. Do not omit a
dependency merely because another fragment also uses it.
'''



# Collected neutral_fragments.py (active implementation only).
ROLE_CONTRACT = '''
The input fragments have NO preassigned roles. Determine each phrase's current
query contribution from the whole request and effective answers, not its ID or
an earlier interpretation. Add role="output" for a requested returned value or
calculation, or role="modifier" for a qualification or result instruction to
every items row. A phrase containing both keeps its complete meaning; do not
invent an extra output from an operand or qualifying entity. If its purpose is
uncertain, retain that uncertainty as missing scope and ask about how this part
should be used, before choosing fields or operations.

Also return edges for direct linguistic dependencies between the supplied IDs:
context: source relies on the entity/calculation supplied by target;
applies_to: source restricts or organizes target. Shared qualifications may
attach to multiple targets. Mere coordination or a schema join creates no edge.
No self-links, duplicates or transitive links. Uncertain links may be omitted.
Override the top-level shape with {"items":[...],"edges":[
{"source_id":"f2","target_id":"f1","kind":"applies_to"}]}.
Every supplied ID must have one items row with its role and all analysis fields.
'''


def validate_fragments(raw, request):
    if (not isinstance(raw, dict) or set(raw) != {'original_text', 'fragments'}
            or raw['original_text'] != request or not isinstance(raw['fragments'], list)
            or not raw['fragments']):
        raise ValueError('Expected unchanged original_text and nonempty fragments')
    seen = set()
    for row in raw['fragments']:
        if (not isinstance(row, dict) or set(row) != {'item_id', 'span'}
                or not isinstance(row['item_id'], str) or not re.fullmatch(r'f[1-9][0-9]*', row['item_id'])
                or row['item_id'] in seen or not isinstance(row['span'], str)
                or not row['span'].strip() or row['span'] not in request):
            raise ValueError('Each fragment needs a unique neutral ID and exact source quote')
        seen.add(row['item_id'])
    return deepcopy(raw)


def validate_mapping(raw, neutral, payload, context):
    if not isinstance(raw, dict) or set(raw) != {'items', 'edges'} or not isinstance(raw['items'], list):
        raise ValueError('Expected items with current roles and edges')
    roles, clean = {}, []
    spans = {r['item_id']: r['span'] for r in neutral['fragments']}
    for row in raw['items']:
        if (not isinstance(row, dict) or row.get('item_id') not in spans
                or row['item_id'] in roles or row.get('role') not in ('output', 'modifier')):
            raise ValueError('Each neutral ID needs exactly one current output/modifier role')
        roles[row['item_id']] = row['role']
        clean.append({k: deepcopy(v) for k, v in row.items() if k != 'role'})
    if set(roles) != set(spans):
        raise ValueError('Missing current roles: ' + ', '.join(sorted(set(spans) - set(roles))))
    frame = QueryFrame(neutral['original_text'],
        tuple(r for r in neutral['fragments'] if roles[r['item_id']] == 'output'),
        tuple(r for r in neutral['fragments'] if roles[r['item_id']] == 'modifier'))
    # Reuse all frozen column, missing-dimension, relation and dependency checks.
    # These deterministic validation calls do not issue any model requests.
    def replay(stage, prompt, data):
        if stage == 'compact_flat':
            return {k: v for k, v in frame.to_json().items() if k != 'gaps'}
        if stage == 'compact_relations':
            return {'edges': deepcopy(raw['edges'])}
        if stage == 'compact_analysis':
            return {'items': deepcopy(clean)}
        if stage.endswith('_contract_retry'):
            raise ValueError(data['contract_error'])
        raise ValueError('Unexpected validation stage: ' + stage)
    return LEGACY_ANALYZE(replay, payload, context)


LEGACY_ANALYZE = frozen_module('compact_analysis').analyze_flat



# Collected progressive_protocol.py (active implementation only).
LINEAGE_PROMPT = '''You are tracing dependency evidence across query revisions.
Review every obligation against current_request, current_nodes, relations and
the supplied actual answers and protocol removals. Source IDs are provenance,
not proof of current ownership. A missing mapping alone cannot retire evidence.

For each obligation choose one action:
- carry: the contribution survives. List every current node that needs this
  dependency. Explain the surviving contribution for each owner, especially if
  that owner has omitted the column. Do not copy all parent dependencies to all
  children merely because the parent split.
- retire: an explicit removal withdrew the contribution or an accepted choice
  superseded it. Select a witness_id from witnesses and explain how that event
  removes THIS dependency. Removing an output does not remove the same column
  needed by a surviving filter/calculation or a shared join.
- unresolved: ownership and retirement are not established. Keep the evidence
  unresolved; do not guess or invent a user choice.

Later events override earlier ones; after_turn gives their dialogue position.
Do not change requirements, select fields, grant access, or generate questions.
Return JSON only: {"effects":[{"obligation_id":"e1","action":"carry",
"current_ids":["f2"],"witness_id":"","reason":"surviving contribution"}]}.
Cover each obligation exactly once. All rows need a nonempty reason. carry needs
nonempty current_ids and empty witness_id. retire needs empty current_ids and
one existing witness_id. unresolved needs both empty. Use only these five keys.
Reference witness IDs; do not reproduce quotations or output event_id/quote.
'''


def lineage_witnesses(payload):
    return [{'witness_id': 'w' + str(i + 1), 'event_id': event['event_id'], 'quote': quote}
            for i, (event, quote) in enumerate((event, quote)
                for event in payload['events'] for quote in event['quotes'])]


def expand_lineage(raw, payload):
    """Bind selected evidence IDs to immutable supplied events and quotations."""
    new = deepcopy(raw)
    if not isinstance(new, dict) or not isinstance(new.get('effects'), list):
        return new
    witnesses = {w['witness_id']: w for w in lineage_witnesses(payload)}
    for row in new['effects']:
        if not isinstance(row, dict) or 'witness_id' not in row:
            # apply_effects validates already expanded legacy rows a second time.
            continue
        if set(row) != {'obligation_id', 'action', 'current_ids', 'witness_id', 'reason'}:
            raise ValueError('Dependency choice requires exactly the five documented keys')
        wid = row.pop('witness_id')
        if not isinstance(wid, str):
            raise ValueError('witness_id must be a string')
        if row['action'] == 'retire':
            if wid not in witnesses:
                raise ValueError('Retirement needs an existing witness_id from witnesses')
            row.update(event_id=witnesses[wid]['event_id'], quote=witnesses[wid]['quote'])
        else:
            if wid:
                raise ValueError('Only retirement may cite a witness_id')
            row.update(event_id='', quote='')
    return new


def normalize_mapping(raw):
    """Remove empty placeholders only for known slots explicitly not missing."""
    new = deepcopy(raw)
    if not isinstance(new, dict) or not isinstance(new.get('items'), list):
        return new
    for row in new['items']:
        if not isinstance(row, dict):
            continue
        focus, missing = row.get('question_focus'), row.get('missing')
        if not isinstance(focus, dict) or not isinstance(missing, list):
            continue
        row['question_focus'] = {k: v for k, v in focus.items()
            if not (k in ('scope', 'field', 'operation', 'value') and k not in missing
                    and isinstance(v, str) and not v.strip())}
    return new


def normalize_lineage(raw, payload):
    """Supply a mechanical audit reason only for already mapped carry columns.

    No dependency or retirement is inferred; semantic retirement and unresolved
    evidence still require model reasons under the existing strict validator.
    """
    new = expand_lineage(raw, payload)
    if not isinstance(new, dict) or not isinstance(new.get('effects'), list):
        return new
    obligations = {o['obligation_id']: o for o in payload['obligations']}
    nodes = {n['item_id']: n for n in payload['current_nodes']}
    for row in new['effects']:
        if not isinstance(row, dict) or row.get('action') != 'carry':
            continue
        oid, owners, reason = row.get('obligation_id'), row.get('current_ids'), row.get('reason')
        if (not isinstance(oid, str) or oid not in obligations or not isinstance(owners, list)
                or not owners or not isinstance(reason, str) or reason.strip()):
            continue
        column = obligations[oid]['column']
        if all(isinstance(i, str) and i in nodes and column in nodes[i]['required_columns'] for i in owners):
            row['reason'] = 'Current mapped owners explicitly retain dependency ' + column + '.'
        elif all(isinstance(i, str) and i in nodes for i in owners):
            missing = [i for i in owners if column not in nodes[i]['required_columns']]
            raise ValueError('Obligation ' + oid + ': carry needs a nonempty reason explaining why '
                + column + ' is required by owners ' + ', '.join(missing)
                + ' which do not declare it. Explain the surviving contribution or correct current_ids; '
                  'ancestry alone is not dependency evidence.')
    return new


def revision_scoped_bundle(original, session, context):
    """Old refusals remain evidence, not vetoes on another inventory's slots."""
    history = session['turns']
    inventory = session['view']['inventory_id']
    session['turns'] = [t for t in history if t['answer']['action'] != 'unanswerable'
                        or t.get('target_inventory_id') == inventory]
    try:
        return original(session, context)
    finally:
        session['turns'] = history


def check_active_state(session):
    """Validate routing references, not the model's interpretation of text."""
    view = session.get('view')
    if not view:
        return
    active = set(view['items'])
    if active & set(view.get('progressive', {}).get('retired', {})):
        raise ValueError('Retired parents cannot remain active fragments')
    for edge in view['relations']['edges']:
        if edge['source_id'] not in active or edge['target_id'] not in active:
            raise ValueError('An active relation refers to a retired or missing fragment')
    for q in view['questions']:
        if q['dimension'] == 'permission':
            continue  # Historical obligations keep their qualified source.
        if q['item']['item_id'] not in active or any(
                slot.split('/')[0] not in active for slot in q.get('requested_slots', [])):
            raise ValueError('A current question refers to a retired or missing fragment')
    keys = {q['key'] for q in view['questions']}
    if set(session.get('menu_frontiers', {})) - keys:
        raise ValueError('A menu frontier survived replacement of its decision')
    if session.get('pending') and session['pending']['decision_key'] not in keys:
        raise ValueError('A pending menu refers to an obsolete decision')



# Collected progressive_dialogue.py (active implementation only).
SIMULATOR_SCOPE_VERSION = 'local_question_scope_v1'


LOCAL_SCOPE_RULE = '''
Identify the single business purpose of the CURRENT public question before
choosing modules or options. Original intent and SQL may contain other outputs
and conditions; those do not become answers to this question merely because a
category or field option can accommodate them. Category navigation has the SAME
purpose as its subsequent concrete field questions, not the whole private query.
multi_select permits several choices for this purpose, not answering several
independent requests at once. Preserve explicit one/several/all wording of the
asked requirement. Cite multiple modules only when they jointly support this
same purpose; a module for a separately requested extra detail belongs to its
own question. If a broad category serves several purposes, choose it only for
the purpose asked now. Do not invent acceptance of other fields. Keep the same
response format and strict acceptance rules; no separate review is performed.
'''


PUBLIC_SCOPE_RULE = ('This menu concerns only the quoted part of the request. '
                     'Keep independently requested information for its own question; '
                     'choosing a category does not expand this question to the whole request.')


def scoped_public_question(question, decision, container_span):
    result = deepcopy(question)
    old = 'Regarding "' + container_span + '":'
    new = 'Regarding "' + decision['quote'] + '":'
    result['question'] = result['question'].replace(old, new, 1)
    if PUBLIC_SCOPE_RULE not in result['question']:
        result['question'] += ' ' + PUBLIC_SCOPE_RULE
    return result



# Collected frozen_history_reduction.py (active implementation only).
REMOVAL_RULES = """
Protocol removals are the only additional exception to preserving original intent.
They are not user answers or consent. Apply removal_history in listed order after
its after_turn index; turn_index refers to the original dialogue position.
Earlier answers cannot restore a requirement removed later.
If pending_removal is present, remove the smallest coherent requirement addressed
by its question and target_fragment. Preserve independent parts of a compound
fragment. Removing an output also removes qualifications/references exclusive to
that output; retain shared conditions on surviving outputs. Removing a condition
preserves its subject and other conditions. Preserve surviving roles, AND/OR,
negation and scope; do not promote a filter entity or operand to a returned value.
Repair grammatical attachments without adding new fields, calculations or values.
Return only the usual JSON {"request":"..."}. For a pending removal only, an empty
request means no query objective remains. Do not explain permissions or add consent.
"""


ANALYSIS_REMOVAL_RULES = """
removal_history records applied protocol revisions, not user answers or consent.
Each revision takes effect after its after_turn dialogue index; list order breaks
ties. current_request is the rebuilt active request after these revisions and
subsequent accepted answers. Analyze that request, not the original intent alone.
Original requests, earlier answers, confirmed_updates and previous nodes are
historical evidence: do not restore a removed requirement merely because they
mention it, or interpret its recorded rejection as keeping it active. Preserve
independent surviving requirements. An explicit later answer may reintroduce a
requirement; its actual dependencies still need the usual permission checks.
For propagation_analysis, use the existing local-update contract: if a removal
requires deleting a node, changing attachments or changing nodes outside update_ids,
return a nonempty fallback_reason and items: []. Otherwise update the surviving
local meaning. Do not keep a removed requirement just to fill an old fragment ID.
Keep the response shape required by the current stage.
"""


class _HistorySession:
    def __init__(self, prompt_profile='frozen'):
        if prompt_profile != 'frozen':
            raise ValueError('This adapter requires frozen prompts')
        self.engine = frozen_module('simple_session')
        self.original = {name: getattr(self.engine, name) for name in
                         ('next_step', 'prepare_menu', 'commit_answer', 'analyze_session', 'render_session')}
        self.original_history = self.engine.public_history
        self.original_reconstruction = self.engine.reconstruction_history

    @contextmanager
    def installed(self):
        before = frozen_hashes()
        with ExitStack() as stack:
            for name in self.original:
                stack.enter_context(patch.object(self.engine, name, getattr(self, name)))
            try:
                yield self
            finally:
                if frozen_hashes() != before:
                    raise RuntimeError('Frozen archive changed')

    def next_step(self, session):
        if session.get('removal_no_query'):
            return {'action': 'refuse', 'reason': 'no_remaining_query_objective',
                    'turn': len(session['turns'])}
        plan = self.original['next_step'](session)
        if plan['action'] in ('blocked', 'deliver') and session.get('lineage_pending'):
            return {'action': 'unresolved', 'reason': 'unresolved_permission_lineage',
                    'turn': len(session['turns'])}
        return plan


    def _removal_history(self, session):
        return [{k: deepcopy(e[k]) for k in
            ('after_turn', 'trigger', 'target_fragment', 'target_role', 'question',
             'asked_dimension', 'previous_request', 'request') if k in e}
            for e in session.get('reduction_audit', [])]

    def _history(self, session):
        history = self.original_history(session)
        for index, turn in enumerate(history['turns'], 1):
            turn['turn_index'] = index
        history['turns'] = [t for t in history['turns']
                            if t['answer']['action'] in ('answer', 'choose')]
        history['removal_history'] = self._removal_history(session)
        return history

    def analyze_session(self, call, session, context, reconstruct=None):
        if session.get('removal_no_query'):
            return session
        def with_removals(stage, prompt, payload):
            base_stage = stage.removesuffix('_contract_retry')
            if session.get('reduction_audit') and base_stage in (
                    'round_reconstruct', 'propagation_analysis', 'compact_flat',
                    'compact_analysis', 'round_analysis', 'neutral_fragments', 'neutral_analysis'):
                # validated_call retains its original payload, so enrich the
                # nested retry input too; never rewrite previous_response.
                payload = deepcopy(payload)
                retry = stage.endswith('_contract_retry')
                active = payload['input'] if retry else payload
                if base_stage == 'round_reconstruct':
                    prompt += REMOVAL_RULES
                    active = self._history(session)
                else:
                    prompt += ANALYSIS_REMOVAL_RULES
                    active['removal_history'] = self._removal_history(session)
                    # Keep every real answer, including unanswerable. The
                    # protocol revision is separate from user evidence.
                    for index, turn in enumerate(
                            active.get('public_conversation', {}).get('turns', []), 1):
                        turn['turn_index'] = index
                if retry:
                    payload['input'] = active
                else:
                    payload = active
            return call(stage, prompt, payload)
        return self.original['analyze_session'](with_removals, session, context, reconstruct)


    def commit_answer(self, call, session, plan, question, answer, context):
        q = self.engine.decision(session, plan)
        new = self.original['commit_answer'](call, session, plan, question, answer, context)
        if len(new['turns']) == len(session['turns']):
            return new  # Still navigating categories; preserve frozen behavior.
        last = new['turns'][-1]
        if last.get('menu_rejected') or last['answer']['action'] == 'unanswerable':
            last.pop('menu_rejected', None)
            return self._remove(call, new, session, q, 'no_accepted_local_answer', context)
        return new

    def prepare_menu(self, call, session, plan, context, max_options=5):
        q = self.engine.decision(session, plan)
        new, public = self.original['prepare_menu'](call, session, plan, context, max_options)
        if public is not None:
            return new, public
        audits = [r for r in new['view']['candidate_audit'] if r.get('decision_key') == q['key']]
        if audits and audits[-1].get('reason') == 'no_authorized_relevant_choices':
            new = self._remove(call, new, session, q, 'empty_candidate_domain', context)
        return new, None



# Collected deletion_rules_probe.py (active implementation only).
DELETE = '''You are an editor applying one deletion to a database query request.
current_request is the active request. pending_removal identifies the local
requirement to remove; it is a protocol event, not user agreement or new intent.
Remove only the asked requirement, not automatically the entire target fragment.
Preserve independently meaningful parts of a compound fragment.

Preserve surviving returned values and calculations as outputs, and surviving
conditions as conditions on the same subjects. Do not turn a condition's entity,
operand or count into another output. Removing one condition does not remove its
subject or other conditions. Separate the base entity from restrictions on it:
remove the targeted restriction, retaining the entity when other requirements
still concern it. Do not invent a replacement population or specify a formerly
unspecified identity, date, threshold, field or calculation.

When removing an output, remove qualifications, references, operands and result
instructions that exist solely for that output. Preserve a shared qualification
on surviving outputs. A field used independently to filter surviving results is
not deleted merely because it is no longer returned. Review dependent chains;
being connected is not sufficient reason to delete another requirement.
Within AND/OR expressions, remove the targeted branch and preserve the remaining
branches and their operators; do not replace a removed branch with True/False.
Resolve dangling pronouns only from an already explicit referent. Repair grammar
without changing surviving roles, scope, negation, quantifiers or ambiguity.
If no requested result remains, return an empty request, not an invented output.

When existing_semantics is supplied, use its nodes and edges to check what is an
output, what qualifies it, and what is shared. It is fallible prior analysis, not
new user intent. Do not add content that occurs only in that analysis and is not
supported by current_request. Deleting a requirement does not authorize silently
repairing unrelated ambiguity or substituting a different requested field.
Return only JSON {"request":"..."}, in the same language as current_request.
'''


RECEIPT = '''
Also report the effect on the supplied inventory. Override the response shape:
{"request":"...","drop_ids":["..."],"revise_ids":["..."]}.
drop_ids identifies nodes with no surviving contribution, including exclusively
dependent qualifications. revise_ids identifies nodes retaining some contribution
whose meaning or attachment changes. Other nodes are retained. Only use supplied
IDs; lists must be disjoint with no duplicates. This receipt does not grant access.
'''


FOCUSED = '''
Deletion scope is the QUESTION'S decision, not a graph node:
1. Interpret pending_removal.question and asked_dimension to identify the exact
   requirement being withdrawn. target_fragment locates it in the request; its
   boundaries are not a command to delete all of its content.
2. Determine what independent meaning inside that same fragment remains after
   withdrawing only that decision. Preserve that remainder even when it shares
   an output/modifier ID with the removed meaning. In a logical condition,
   a question about one branch withdraws that branch, not the other branches.
3. Only if no contribution remains may the entire node and its exclusively
   dependent qualifications disappear. Review dependencies AFTER determining
   the remainder, never by deleting the whole node first.
4. A surviving value/calculation keeps the entity it describes. Deleting an
   association used to specify that entity does not delete the base entity;
   restate its explicit noun rather than leaving a pronoun without an antecedent.
Do not infer deletion scope from item_id, role, or connectivity alone. Return only
the request JSON already specified; no explanatory reasoning is required.
'''


COHERENT = '''
Withdraw the smallest COMPLETE requested contribution that requires the rejected
decision. Rejecting a required field removes its output component or its predicate
branch, including that branch's dependent operator and value. It does not merely
erase the field's name while leaving an unspecified version of the same demand.
Likewise, an unanswered essential computation decision retires the dependent
calculation if no independently requested remainder can be expressed.
The rewritten request must not still require an answer to the withdrawn decision.
Determine this complete contribution inside target_fragment; never equate it
with the entire graph node when independent outputs or predicate branches remain.
Do not remove an independently requested output just because a removable filter
on it is withdrawn. Keep any base entity needed to describe the remaining outputs.
'''


def check(raw, case, arm):
    with_receipt = arm in ('receipt', 'coherent_receipt')
    keys = {'request', 'drop_ids', 'revise_ids'} if with_receipt else {'request'}
    if not isinstance(raw, dict) or set(raw) != keys or not isinstance(raw['request'], str):
        raise ValueError('Wrong response shape')
    if with_receipt:
        ids = {n['item_id'] for n in case['existing_semantics']['nodes']}
        for key in ('drop_ids', 'revise_ids'):
            values = raw[key]
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError('Expected ID array')
            if len(set(values)) != len(values) or not set(values) <= ids:
                raise ValueError('Unknown or repeated ID')
        if set(raw['drop_ids']) & set(raw['revise_ids']):
            raise ValueError('Drop and revise must be disjoint')
    return raw


def deletion_effects(graph, drop_ids, revise_ids):
    """Plan mechanical effects only; this is NOT an executable updated graph.

    Model proposals must first receive semantic validation. Nodes with surviving
    content stay subject to analysis/access checks; adjacency never deletes them.
    """
    known = {n['item_id'] for n in graph['nodes']}
    dropped, revised = set(drop_ids), set(revise_ids)
    if not dropped | revised <= known or dropped & revised:
        raise ValueError('Invalid deletion receipt')
    removed_edges = [deepcopy(e) for e in graph['edges']
                     if e['source_id'] in dropped or e['target_id'] in dropped]
    affected = revised | {i for e in removed_edges
                          for i in (e['source_id'], e['target_id']) if i not in dropped}
    return {'retired_ids': sorted(dropped), 'review_ids': sorted(affected),
            'removed_edges': removed_edges,
            'requires_dependency_and_permission_reanalysis': True}



# Collected coherent_deletion_session.py (active implementation only).
DELETION_VERSION = 'coherent_deletion_receipt_full_v1'


HISTORICAL = '''
Nodes marked evidence_only are unresolved historical requirements, not extra
outputs to introduce. Their IDs are separate from the current inventory, even if
their source_id matches a current node. Review whether their meaning survives in
current_request after this deletion. Drop such evidence only when its complete
contribution has been withdrawn by this or an already applied removal_history
event. Mere omission by an earlier rewrite is NOT evidence of withdrawal.
Keep it when any of that contribution survives or when withdrawal is uncertain.
A historical target may be dropped without changing text if an earlier recorded
removal already withdrew it. Never restore it just to remove it again.
Current node drop_ids also retire recorded obligations owned by that exact
inventory node. Partial revisions do not retire the whole obligation.
'''


def receipt_inventory(session, view):
    """Attach evidence only by qualified identity; no cross-inventory ID guesses."""
    graph = {'nodes': [{k: deepcopy(n[k]) for k in
        ('item_id', 'span', 'role', 'requirement')} for n in view['items'].values()],
        'edges': deepcopy(view['relations']['edges'])}
    owners = {}
    used = set(view['items'])
    for key, issue in evidence_sources(session).items():
        iid = issue['item_id']
        current = (issue.get('inventory_id') is not None
            and issue.get('inventory_id') == view.get('inventory_id')
            and iid in view['items'])
        if current:
            owners[key] = iid
            continue
        alias = 'historical_' + key
        while alias in used:
            alias += '_'
        used.add(alias)
        owners[key] = alias
        graph['nodes'].append({'item_id': alias, 'source_id': iid,
            'source_inventory': issue.get('inventory_id'), 'evidence_only': True,
            **{k: deepcopy(issue[k]) for k in ('span', 'role', 'requirement')}})
    return graph, owners


class _DeletionSession(_HistorySession):
    def validate_deletion_receipt(self, raw, graph):
        check(raw, {'existing_semantics': graph}, 'coherent_receipt')

    def _remove(self, call, session, basis, q, trigger, context):
        view = basis.get('view')
        if view is None:
            raise ValueError('Deletion requires a current inventory')
        graph, owners = receipt_inventory(session, view)
        # A historical permission task must never attach to a reused local ID.
        target = (owners.get(q.get('key')) if q['dimension'] == 'permission' else None)
        if target is None:
            target = q['item']['item_id']
            node = view['items'].get(target)
            if node is None or (node['span'], node['role']) != (q['item']['span'], q['item']['role']):
                raise ValueError('Deletion target has no qualified inventory identity')
        event = {'after_turn': len(session['turns']), 'trigger': trigger,
            'target_fragment': q['item']['span'], 'target_role': q['item']['role'],
            'question': q['focus'], 'asked_dimension': q['dimension'],
            'target_receipt_id': target}
        payload = {'current_request': session['current_request'],
            'pending_removal': event, 'existing_semantics': graph,
            'removal_history': self._removal_history(session)}
        historical = {n['item_id'] for n in graph['nodes'] if n.get('evidence_only')}
        def validate(raw):
            self.validate_deletion_receipt(raw, graph)
            validate_removal_objective(raw, graph, getattr(self, '_removal_decision', None), target)
            dropped, revised = set(raw['drop_ids']), set(raw['revise_ids'])
            if target not in dropped | revised:
                raise ValueError('The targeted contribution must be dropped or revised')
            if raw['request'].strip() == session['current_request'].strip():
                if target not in historical or target not in dropped:
                    raise ValueError('Active requirement deletion left the request unchanged')
            if not raw['request'].strip():
                outputs = {n['item_id'] for n in graph['nodes'] if n['role'] == 'output'}
                if not outputs <= dropped:
                    raise ValueError('Empty request cannot retain output contributions')
            return deepcopy(raw)
        receipt = frozen_module('analysis').validated_call(call, 'deletion_reconstruct',
            DELETE + FOCUSED + COHERENT + RECEIPT + HISTORICAL, payload, validate)
        text = receipt['request'].strip()
        dropped = set(receipt['drop_ids'])
        retired = sorted(k for k, owner in owners.items() if owner in dropped)
        effects = deletion_effects(graph, receipt['drop_ids'], receipt['revise_ids'])
        new = deepcopy(session)
        new.setdefault('reduction_audit', []).append({**event,
            'previous_request': session['current_request'], 'request': text,
            'method': DELETION_VERSION, 'receipt': receipt, 'effects': effects,
            'source_inventory': view.get('inventory_id'), 'permission_owners': owners,
            'retired_permission_issues': {k: evidence_sources(session)[k] for k in retired},
            'analysis_mode': 'full_after_deletion'})
        new['permission_issues'] = {k: v for k, v in new['permission_issues'].items() if k not in retired}
        for key in retired:
            new.get('lineage_pending', {}).pop(key, None)
        if not new.get('lineage_pending'):
            new.pop('lineage_pending', None)
        new['current_request'] = text
        new['pending'], new['menu_frontiers'] = None, {}
        new['view'] = None
        new.pop('update_base', None)
        new['removal_no_query'] = not text
        if not text:
            new['menu_blockers'] = {}
            return new
        # No session mutation escapes before fresh analysis and permission checks
        # succeed. Full analysis intentionally replaces obsolete routing state.
        result = self.analyze_session(call, new, context, lambda *_: text)
        result['view']['update_audit'] = {'mode': 'deletion_full',
            'receipt': receipt, 'retired_issue_keys': retired}
        return result



# Collected deletion_focus_probe.py (active implementation only).
FOCUS = '''
blocked_dependency_focus is internal evidence explaining which dependencies of
this decision cannot be supported, not a new user requirement. For a permission
decision its generic question does not mean that every contribution in the
target fragment is rejected. Locate the smallest complete output/predicate
contribution requiring the blocked dependencies; preserve independent branches
using other dependencies. A shared join must be considered wherever it is needed.
Do not delete an otherwise independent branch merely because the old analysis
packed it into the same node. If part of that node survives, mark it revised.
Do not expose the internal dependency or access explanation in the returned text.
'''



# Collected focused_deletion_session.py (active implementation only).
EVIDENCE_VERSION = 'focused_deletion_lineage_v3_1_1'


BUSINESS_FOCUS = 'Which business information should be used for this part?'


class _EvidenceSession(_DeletionSession):
    @contextmanager
    def installed(self):
        module = frozen_module('hierarchical_questions')
        original_prepare = module.prepare

        def public_neutral_prepare(*args, **kwargs):
            state, public = original_prepare(*args, **kwargs)
            if public is None:
                return state, public
            plan = args[2] if len(args) > 2 else kwargs['plan']
            q = self.engine.decision(args[1] if len(args) > 1 else kwargs['session'], plan)
            if q['dimension'] == 'permission':
                original_focus = q['focus']
                safe_focus = BUSINESS_FOCUS
                public['question'] = public['question'].replace(original_focus, safe_focus)
                public['focus'] = safe_focus
                state['pending']['public'] = deepcopy(public)
            return state, public

        session_update = frozen_module('session_update')
        original_finish = session_update.finish_view
        def neutral_finish(*args, **kwargs):
            state = args[0] if args else kwargs['session']
            context = args[1] if len(args) > 1 else kwargs['context']
            working = deepcopy(state)
            view = working['view']
            # The frozen compatibility merger rewrites an old issue with a
            # current node's meaning. Preserve source evidence BEFORE that step.
            # Only supported same-inventory dependencies may be refreshed there.
            historical = {}
            for key, issue in list(working['permission_issues'].items()):
                node = view['items'].get(issue['item_id'])
                if (node is None or issue.get('inventory_id') != view['inventory_id']
                        or not set(issue['denied_columns']) <= set(node['required_columns'])):
                    historical[key] = working['permission_issues'].pop(key)
            result = original_finish(working, context)
            for key, issue in historical.items():
                if key in result['permission_issues']:
                    # The same inventory ID can have a revised meaning. Preserve
                    # the old source separately from the newly detected issue.
                    key = 'source:' + digest([key, issue])
                result['permission_issues'][key] = issue
            if result.get('strict_menus') and result.get('view'):
                for q in result['view'].get('questions', []):
                    if q['dimension'] == 'permission':
                        q['focus'] = BUSINESS_FOCUS
            state.clear()
            state.update(result)
            return state

        with (super().installed(), patch.object(module, 'prepare', public_neutral_prepare),
              patch.object(session_update, 'finish_view', neutral_finish)):
            yield self

    def next_step(self, session):
        plan = super().next_step(session)
        if (plan['action'] == 'deliver' and plan.get('reason') != 'user_requested_submission'
                and not has_query_objective(session['view']['items'].values())):
            return {'action': 'unresolved', 'reason': 'no_query_objective', 'turn': len(session['turns'])}
        # No ready task is not proof of business impossibility. In particular,
        # old permission records and the pending ledger must share one boundary.
        if plan['action'] == 'blocked':
            return {'action': 'unresolved', 'reason': 'unresolved_access_state',
                    'turn': len(session['turns'])}
        if plan['action'] == 'deliver' and (session.get('pending') or session.get('menu_frontiers')):
            return {'action': 'unresolved', 'reason': 'unfinished_menu',
                    'turn': len(session['turns'])}
        if plan['action'] == 'deliver' and any(
                set(n['required_columns']) - set(session['allowed'])
                for n in session['view']['items'].values()):
            return {'action': 'unresolved', 'reason': 'unsynchronized_current_dependencies',
                    'turn': len(session['turns'])}
        return plan

    def render_session(self, call, session):
        plan = self.next_step(session)
        if plan['action'] == 'unresolved':
            return {'action': 'system_error', 'reason': plan['reason'], 'text': '',
                    'available_for_text2sql': False, 'access_control': 'not_evaluated'}
        result = self.original['render_session'](call, session)
        if result['action'] == 'deliver' and session.get('reduction_audit'):
            result['method'] = 'history_with_protocol_reductions'
        return result

    def analyze_session(self, call, session, context, reconstruct=None):
        analyzed = reconcile(call, super().analyze_session(call, session, context, reconstruct), context)
        if self.next_step(analyzed)['action'] != 'unresolved':
            return analyzed
        # One full synchronization for this semantic revision can recover an
        # omitted current gap. It cannot invent a user answer or clear evidence.
        revision = digest([analyzed['current_request'], analyzed['turns'],
                           self._removal_history(analyzed), analyzed['allowed']])
        if analyzed.get('lineage_resync_signature') == revision:
            return analyzed
        retry = deepcopy(analyzed)
        retry['view'] = None
        retry['pending'], retry['menu_frontiers'] = None, {}
        retry.pop('update_base', None)
        recovered = self._resynchronize(call, retry, analyzed, context)
        recovered = reconcile(call, recovered, context)
        recovered['lineage_resync_signature'] = revision
        recovered.setdefault('lineage_resync_audit', []).append({
            'revision': revision, 'before': self.next_step(analyzed),
            'after': self.next_step(recovered), 'mode': 'full_current_text_once'})
        return recovered

    def _resynchronize(self, call, retry, analyzed, context):
        return super().analyze_session(call, retry, context, lambda *_: analyzed['current_request'])

    def _remove(self, call, session, basis, q, trigger, context):
        target = q['item']
        focus = {'source_inventory': target.get('inventory_id', basis['view']['inventory_id']),
            'target_id': target['item_id'], 'trigger': trigger,
            'dependencies': [{'column': col, 'meaning': context.get('column_language', {}).get(col, ''),
                'blocked': col in target['denied_columns']} for col in target['required_columns']]}
        def with_focus(stage, prompt, payload):
            if stage.removesuffix('_contract_retry') == 'deletion_reconstruct':
                payload = deepcopy(payload)
                active = payload['input'] if stage.endswith('_contract_retry') else payload
                active['blocked_dependency_focus'] = focus
                prompt += FOCUS
            return call(stage, prompt, payload)
        result = super()._remove(with_focus, session, basis, q, trigger, context)
        result['reduction_audit'][-1].update(method=EVIDENCE_VERSION, blocked_dependency_focus=focus)
        return result



# Collected scoped_permission_session.py (active implementation only).
REMOVAL_SCOPE = '''
scoped_permission_decision identifies the precise contribution being removed.
Its quote and requirement narrow pending_removal.target_fragment, which is only
the containing node's source span. Preserve the independent remainder of that
node, especially its returned information when removing an embedded condition.
preserve_output_owner identifies a container that must be revised, not dropped:
an independent contribution survives outside the current decision. Preserve
remaining_requirements in their original roles, whether outputs or conditions.
retained_columns describe independently retained dependencies. Keep their actual
business requirements; do not invent outputs for join keys. Keep shared conditions
on other outputs. Never delete the entire source span solely because one part
has no accepted alternative. Apply only this scoped removal in this transaction.
'''


HISTORY = '''
scoped_permission_answers records the exact business contribution targeted by
each permission menu and its effective turn_index. The original fragment can
contain other requirements. Apply selected fields ONLY to this decision in its
stated role; retain other contributions, including retained_columns. Do not
replace an output when the question concerned a condition within that output's
fragment. Unanswered operations/values remain unspecified. These records do not
grant access: re-evaluate all actual dependencies after reconstruction.
The same records may describe ordinary field decisions. remaining_requirements
lists the other unresolved contributions of that source fragment. An answer to
one field decision does not settle those contributions. Preserve them in the
request and analyze their remaining scope/field/operation/value choices separately.
Completed choices for one purpose do not preclude another field decision for a
different purpose in the same fragment. Never turn an unasked filter into output.
'''


def validate_decision(raw, source, request, context, *, field=False):
    keys = {'quote', 'requirement', 'role', 'kind', 'scope_tables',
            'required_columns', 'question', 'field_candidates'}
    if field:
        keys.add('remaining_requirements')
    if not isinstance(raw, dict) or set(raw) != keys:
        raise ValueError('Expected the documented scoped business decision')
    for k in ('quote', 'requirement', 'question'):
        if not isinstance(raw[k], str) or not raw[k].strip():
            raise ValueError('Decision text must be nonempty')
    if raw['quote'] not in request:
        raise ValueError('Decision quote must occur in current_request')
    if raw['role'] not in ('output', 'modifier') or raw['kind'] not in (
            'attribute', 'calculation', 'relation', 'instruction'):
        raise ValueError('Invalid decision role or kind')
    tables, columns, _ = frozen_module('analysis').indexes(context)
    for k, domain in [('scope_tables', tables), ('required_columns', source['required_columns'])]:
        if (not isinstance(raw[k], list) or (not raw[k] and (k == 'scope_tables' or not field))
                or any(not isinstance(v, str) or v not in domain for v in raw[k])
                or len(set(raw[k])) != len(raw[k])):
            raise ValueError('Invalid scoped ' + k)
    if not field and not set(raw['required_columns']) & set(source['denied_columns']):
        raise ValueError('Decision must address a pending dependency')
    if field and (not isinstance(raw['remaining_requirements'], list)
                  or any(not isinstance(s, str) or not s.strip() for s in raw['remaining_requirements'])):
        raise ValueError('remaining_requirements must contain nonempty business descriptions')
    if not isinstance(raw['field_candidates'], list):
        raise ValueError('Expected field_candidates array')
    seen = set()
    for c in raw['field_candidates']:
        if (not isinstance(c, dict) or set(c) != {'column', 'label'}
                or not isinstance(c['column'], str) or c['column'] not in columns
                or c['column'] in seen or not isinstance(c['label'], str) or not c['label'].strip()):
            raise ValueError('Candidate needs a unique schema column and business label')
        seen.add(c['column'])
    return deepcopy(raw)


class _MenuSession(_EvidenceSession):
    def __init__(self, prompt_profile='frozen', *, scope_fields=False):
        super().__init__(prompt_profile)
        self.scope_fields = scope_fields




    def preserved_owner(self, owner, decision):
        return owner['item_id'] if owner['role'] == 'output' and decision['role'] == 'modifier' else None

    @contextmanager
    def installed(self):
        module = frozen_module('hierarchical_questions')
        original = module._choices
        updates = frozen_module('session_update')
        original_groups = updates.validate_groups
        def optional_groups(raw, session):
            try:
                return original_groups(raw, session)
            except (ValueError, TypeError, KeyError) as exc:
                # Shared-answer links are optional hints. An invalid group must
                # not clear a gap or abort independently answerable questions.
                session['view'].setdefault('shared_decision_audit', []).append({
                    'action': 'discard_invalid_groups', 'reason': str(exc),
                    'response': deepcopy(raw)})
                return []
        def scoped_choices(call, session, q, context):
            decision = q.get('scoped_permission_decision')
            if not decision:
                return original(call, session, q, context)
            if q['dimension'] == 'scope':
                return original(call, session, q, context)
            # Keep ordinary code authorization and connectivity checks.
            if not q['item']['field_candidates']:
                return [], True
            return original(call, session, {**q, 'dimension': 'field'}, context)
        with (super().installed(), patch.object(module, '_choices', scoped_choices),
              patch.object(updates, 'validate_groups', optional_groups)):
            yield self

    def prepare_menu(self, call, session, plan, context, max_options=5):
        q = self.engine.decision(session, plan)
        work = deepcopy(session)
        field = self.scope_fields and q['dimension'] == 'field'
        scope = self.scope_decision_needed(q)
        if (q['dimension'] == 'permission' or field or scope) and not q.get('scoped_permission_decision'):
            source = q['item']
            payload = {'current_request': session['current_request'],
                'source': source, 'pending_columns': source['denied_columns'],
                'public_conversation': self._history(session),
                'schema': {k: context[k] for k in ('tables', 'foreign_keys')},
                'column_meanings': context.get('column_language', {})}
            decision = self.compile_decision(call, payload, context, field=field, scope=scope)
            q = next(r for r in work['view']['questions'] if r['key'] == plan['question_id'])
            retained = sorted(set(source['required_columns']) - set(decision['required_columns']))
            candidates = [c for c in decision['field_candidates']
                          if q['dimension'] != 'permission' or c['column'] not in retained]
            replaceable = sorted(set(source['required_columns']) - set(retained))
            q['scoped_permission_decision'] = {**decision,
                'retained_columns': retained, 'replaceable_columns': replaceable}
            # Keep source span/ID for update provenance. Only this question's
            # projection changes; the graph and evidence ledger are untouched.
            q['item'] = {**deepcopy(source), **{k: decision[k] for k in
                ('role', 'kind', 'requirement', 'scope_tables', 'required_columns')},
                # Search scopes describe alternatives, not simultaneous joins.
                # Derive common obligations from the active source, never from
                # the compiler's unselected candidate directions or join guesses.
                'menu_required_columns': self.engine.menu_dependency_columns(
                    source, decision, q['dimension']),
                'denied_columns': sorted(set(decision['required_columns']) - set(work['allowed'])),
                'missing': ['field'], 'question_focus': {'field': decision['question']},
                'field_candidates': candidates}
            q['focus'] = decision['question']
            original_dimension = q['dimension']
            dimension = self.decision_dimension(decision, original_dimension)
            q['dimension'] = dimension
            slot = 'field' if dimension == 'permission' else dimension
            q['item']['missing'] = [slot]
            q['item']['question_focus'] = {slot: decision['question']}
            if dimension != original_dimension:
                q['requested_slots'] = [source['item_id'] + '/' + dimension]
            # Keep a stable plan while narrowing its semantic target.
        new, public = super().prepare_menu(call, work, plan, context, max_options)
        if public is not None and q.get('scoped_permission_decision'):
            # Parent's privacy guard uses a generic focus; replace it with the
            # scoped business wording, never a column/access diagnostic.
            focus = q['scoped_permission_decision']['question']
            public['question'] = public['question'].replace(public['focus'], focus, 1)
            public['focus'] = focus
            if field:
                source_prefix = 'Regarding "' + q['item']['span'] + '":'
                public['question'] = public['question'].replace(source_prefix,
                    'Regarding "' + q['scoped_permission_decision']['quote'] + '":', 1)
            new['pending']['public'] = deepcopy(public)
        return new, public

    def _remove(self, call, session, basis, q, trigger, context):
        decision = q.get('scoped_permission_decision')
        if not decision:
            return super()._remove(call, session, basis, q, trigger, context)
        owner = basis['view']['items'].get(q['item']['item_id'])
        issue = basis.get('permission_issues', {}).get(q['key'], {})
        same_owner = (owner is not None and owner['span'] == q['item']['span']
            and (q['dimension'] in ('field', 'scope') or issue.get('inventory_id') == basis['view']['inventory_id']))
        preserve = self.preserved_owner(owner, decision) if same_owner else None
        def scoped_remove(stage, prompt, payload):
            active = None
            if stage.removesuffix('_contract_retry') == 'deletion_reconstruct':
                payload = deepcopy(payload)
                active = payload['input'] if stage.endswith('_contract_retry') else payload
                active['scoped_permission_decision'] = deepcopy(decision)
                active['preserve_output_owner'] = preserve
                prompt += REMOVAL_SCOPE
            raw = call(stage, prompt, payload)
            if active is not None and preserve and isinstance(raw, dict):
                if preserve in raw.get('drop_ids', []) or preserve not in raw.get('revise_ids', []):
                    # Use the existing contract repair, without committing a
                    # receipt that contradicts the scope of this transaction.
                    return {'scoped_removal_error': 'Revise output owner ' + preserve
                            + '; this local removal cannot drop its independent remainder',
                            'previous_response': raw}
            return raw
        source_q = deepcopy(q)
        if q['dimension'] in ('field', 'scope') and same_owner:
            # The deletion receipt identifies the graph container. Its semantic
            # edit scope remains the separate business-decision evidence.
            source_q['item']['role'] = owner['role']
        result = super()._remove(scoped_remove, session, basis, source_q, trigger, context)
        result['reduction_audit'][-1]['scoped_permission_decision'] = deepcopy(decision)
        return result

    def commit_answer(self, call, session, plan, question, answer, context):
        q = self.engine.decision(session, plan)
        result = super().commit_answer(call, session, plan, question, answer, context)
        if (q.get('scoped_permission_decision') and len(result['turns']) > len(session['turns'])
                and result['turns'][-1]['answer']['action'] == 'choose'):
            result['turns'][-1]['scoped_permission_decision'] = deepcopy(q['scoped_permission_decision'])
            # target_role identifies the containing node for propagation. The
            # decision's own role belongs in its separate evidence record.
            owner = session['view']['items'].get(q['item']['item_id'])
            if owner and owner['span'] == q['item']['span']:
                result['turns'][-1]['target_role'] = owner['role']
        return result

    def analyze_session(self, call, session, context, reconstruct=None):
        evidence = [{'turn_index': i, 'decision': t['scoped_permission_decision'],
                     'selected_options': t.get('selected_options', [])}
                    for i, t in enumerate(session['turns'], 1) if t.get('scoped_permission_decision')]
        def scoped_call(stage, prompt, payload):
            if evidence and stage.removesuffix('_contract_retry') in (
                    'round_reconstruct', 'propagation_analysis', 'compact_analysis', 'neutral_analysis'):
                payload = deepcopy(payload)
                active = payload['input'] if stage.endswith('_contract_retry') else payload
                active['scoped_permission_answers'] = evidence
                prompt += HISTORY
            return call(stage, prompt, payload)
        return super().analyze_session(scoped_call, session, context, reconstruct)



# Collected progressive_decisions.py (active implementation only).
LOCAL_DECISION_PROMPT = '''You are locating the next local business decision in a coarse query fragment.
The fragment is a container, possibly containing several outputs and conditions.
Its role, tables and proposed question are provisional interpretations. Determine
ONE purpose from current_request and effective dialogue. Do not ask for all the
container's fields or remove requirements in this call.

For a permission_decision, locate the contribution USING the pending columns.
A dependency used to restrict the population belongs to that restriction, not
automatically to the returned fields. Include the full calculation when an
operand has no independent meaning. Keep independent contributions outside this
decision even if they share the same source ID.
For a field_decision, choose one still-unspecified output, filter measure or
calculation's operands. Never combine output choices with filter choices.
For a scope_decision, locate the ONE purpose whose entity/population is unclear;
do not automatically ask for the entire container's scope. If public evidence
already fixes that scope, use fixed and prepare its still-unanswered field
question. This never answers or deletes the independent remainder.

Return JSON only, exactly these keys:
quote: nonempty exact current_request substring locating the local purpose;
requirement: its business meaning, preserving stated values and calculations;
role: output or modifier, for this purpose only;
kind: attribute, calculation, relation or instruction;
scope_status: fixed or unresolved;
scope_tables: semantic search scope for THIS purpose's entity and candidate fields,
not mandatory simultaneous query dependencies. Do not copy all container tables:
tables mentioned only by other conditions/outputs belong to the remainder.
Candidate storage tables are not automatically entity anchors. If the entity
itself is genuinely uncertain, use unresolved and list the possible scopes;
required_columns: already determined dependencies of THIS purpose, a subset of
source.required_columns. Alternatives are not dependencies. Permission decisions
must include at least one pending column; ordinary field decisions may use [];
question: with fixed scope, ask WHICH business fields/measures to use for this
purpose; with unresolved scope, ask WHICH business entity/scope this part concerns.
Preserve explicit one/several/all wording for this purpose; distinguish it from
separately requested outputs. Do not ask for actual values, formulas or returned records here;
field_candidates: all schema-supported relevant fields for the fixed local
purpose, as {"column":"table.column","label":"business meaning"}. Do not filter
by permissions. [] when scope is unresolved or no suitable fields exist;
remaining_requirements: exact nonempty contiguous quotes from source.span for
OTHER independently surviving contributions INSIDE this source fragment,
including clear outputs, vague conditions and confirmed choices. Use [] when
this source has no independent remainder. Requirements in OTHER fragments are
outside this list: they survive separately and do not prevent this source from
being removed. Do not paraphrase these quotes or list the current purpose itself.
These quotes preserve local meaning; they do not assign output/modifier roles.
Include only independently surviving requirements: a qualification exclusive to
the removed output is part of that output's removal, not a surviving objective.
Keep shared qualifications only when they still apply to another objective.

No SQL, permissions in public wording, external workarounds, inferred user
choices, extra outputs, or invented computations. Schema presence is not user
intent. A named entity used by a condition is not an additional returned value.
'''


def _compile_business_decision(call, payload, context, *, field, scope=False):
    stage = 'scope_decision' if scope else 'field_decision' if field else 'permission_decision'
    enriched = {**deepcopy(payload), 'decision_type': stage}
    def check(raw):
        if not isinstance(raw, dict) or raw.get('scope_status') not in ('fixed', 'unresolved'):
            raise ValueError('Decision requires scope_status fixed or unresolved')
        status = raw['scope_status']
        core = {k: deepcopy(v) for k, v in raw.items() if k != 'scope_status'}
        decision = validate_decision(core, payload['source'], payload['current_request'], context, field=True)
        # This list controls whether the current container can be retired.
        # A free description of a different node must not pin an empty parent.
        outside = [q for q in decision['remaining_requirements']
                   if q not in payload['source']['span']]
        if outside:
            raise ValueError('remaining_requirements must quote only independent remainder inside '
                'source.span; other fragments are preserved separately. Invalid quotes: ' + repr(outside))
        if len(set(decision['remaining_requirements'])) != len(decision['remaining_requirements']):
            raise ValueError('remaining_requirements quotes must be unique')
        if not field and not scope and not set(decision['required_columns']) & set(payload['pending_columns']):
            raise ValueError('Permission purpose must own at least one pending dependency')
        if status == 'unresolved' and decision['field_candidates']:
            raise ValueError('Resolve business scope before proposing concrete fields')
        return {**decision, 'scope_status': status}
    return frozen_module('analysis').validated_call(call, stage, LOCAL_DECISION_PROMPT, enriched, check)



# Collected progressive_fragments.py (active implementation only).
VERSION = 'progressive_fragments_v6_7'


COARSE = '''You are a language analyst grouping a query into coarse requirements.
Copy one contiguous phrase per broad request or independently stated objective.
Keep an objective with its attached conditions; do not preemptively enumerate
every field, operand or condition. Preserve all requirements, uncertainty and
AND/OR scope. Do not rewrite, choose schema fields, or assign output/modifier roles.
Return only JSON: {"original_text":"exact current_request",
"fragments":[{"item_id":"f1","span":"exact contiguous quote"}]}.
Use unique f1,f2,... IDs. A single fragment for a single combined request is fine.
'''


REFINE = '''You are a language analyst refining requirements after a dialogue event.
Read the current request, the real question and answer or protocol removal, and
the editable source fragments. Return their current wording as local pieces.
Split a fragment only where its contributions now need independent treatment:
separate a confirmed returned value from a still-unanswered qualification, or a
removable condition from a surviving objective. Keep a calculation and its
operands together unless this event requires separate treatment. Keep AND/OR,
negation, scope and shared qualifications. An answer to one part does not answer
the remainder. Do not invent details or turn a condition/entity into an output.
Do not assign roles. Copy nonempty contiguous quotes from current_request.
Copy each piece character-for-character, including punctuation. The editable
fragment spans are PREVIOUS wording for provenance, not the source to copy.
If a fine split is difficult, use one longer exact quote from current_request
covering that parent's surviving contribution; do not copy obsolete wording.

Return only JSON: {"pieces":[{"parent_id":"existing editable ID","span":"quote"}]}.
For each editable parent retain one piece if no split is needed, or several
pieces for its distinct surviving contributions. Do not duplicate a parent
heading as a child alongside the children that already express it. Do not merge
different parents. Boundary fragments are read-only context, never return them.
Every parent must have at least one piece except IDs in removed_ids, which must
have no pieces. Removal is a protocol event, not a user answer. Never restore
removed information from old fragments or earlier answers.
'''


MAP = '''
Only analyze the listed editable fragments, one items row per editable ID.
Boundary nodes are read-only context and must not occur in items. Return all
current linguistic edges touching an editable ID; outside-to-outside edges are
kept by code. Parent IDs are provenance, not role or answer inheritance. Use the
question's actual purpose to assign selected fields; do not apply its answer to
every child of the old fragment. Keep unasked remainder unresolved. Clear a
missing decision only with evidence from the request or effective answers.
'''


def refine_inventory(raw, base, editable, removed, request):
    """Check locality/coverage and allocate stable, role-neutral identities."""
    if not isinstance(raw, dict) or set(raw) != {'pieces'} or not isinstance(raw['pieces'], list):
        raise ValueError('Expected pieces array')
    editable, removed = set(editable), set(removed)
    if not removed <= editable or not editable <= set(base['items']):
        raise ValueError('Invalid refinement region')
    counts, seen = Counter(), set()
    for piece in raw['pieces']:
        if (not isinstance(piece, dict) or set(piece) != {'parent_id', 'span'}
                or not isinstance(piece['parent_id'], str) or piece['parent_id'] not in editable - removed
                or not isinstance(piece['span'], str) or not piece['span'].strip()):
            raise ValueError('Pieces need a surviving editable parent and an exact current quote')
        if piece['span'] not in request:
            raise ValueError('Piece span is not an exact contiguous quote from current_request: '
                + repr(piece['span']) + '. Copy the CURRENT wording character-for-character; '
                'a longer exact quote covering this contribution is allowed. Previous fragment '
                'wording is provenance and must not be copied when absent from current_request.')
        key = (piece['parent_id'], piece['span'])
        if key in seen:
            raise ValueError('Duplicate child meaning/source quote')
        seen.add(key)
        counts[piece['parent_id']] += 1
    if set(counts) != editable - removed:
        raise ValueError('Unanswered remainder cannot disappear: every surviving parent needs pieces')
    history = deepcopy(base.get('progressive', {'retired': {}, 'parents': {}}))
    used = set(base['items']) | set(history['retired']) | set(history['parents'])
    next_id = max([int(i[1:]) for i in used if i.startswith('f') and i[1:].isdigit()] + [0]) + 1
    fragments = [{'item_id': i, 'span': n['span']} for i, n in base['items'].items() if i not in editable]
    replacements = {i: [] for i in editable}
    for piece in raw['pieces']:
        parent = piece['parent_id']
        if counts[parent] == 1 and piece['span'] == base['items'][parent]['span']:
            iid = parent
        else:
            # A narrowed/reworded source is a new leaf even if only one child
            # survives. Its old field refusal must not suppress another purpose
            # that happened to share the former composite node ID.
            iid = 'f' + str(next_id)
            next_id += 1
            history['parents'][iid] = parent
        fragments.append({'item_id': iid, 'span': piece['span']})
        replacements[parent].append(iid)
    for parent, children in replacements.items():
        if children != [parent]:
            history['retired'][parent] = {
                'span': base['items'][parent]['span'], 'children': children}
    return fragments, replacements, history


def analysis_row(node):
    return {k: deepcopy(node[k]) for k in ('item_id', 'role', 'kind', 'requirement',
        'scope_tables', 'required_columns', 'missing', 'question_focus', 'field_candidates')} | {
        'grounded': bool(node['required_columns']), 'reason': node.get('reason', 'Unchanged boundary contribution')}


class MainMethod(_MenuSession):
    def __init__(self, prompt_profile='frozen', *, scope_fields=True, clarity_first=False):
        super().__init__(prompt_profile, scope_fields=scope_fields)
        self.clarity_first = clarity_first
        self._base = None
        self._event = None
        self._session = None
        self._removal_decision = None
        self._preparing = None

    def compile_decision(self, call, payload, context, *, field, scope=False):
        return _compile_business_decision(call, payload, context, field=field, scope=scope)

    def scope_decision_needed(self, question):
        return question['dimension'] == 'scope'

    def decision_dimension(self, decision, original):
        if decision['scope_status'] == 'unresolved':
            return 'scope'
        return 'field' if original == 'scope' else original

    def preserved_owner(self, owner, decision):
        # A coarse ID is a container, not the scope of a removal transaction.
        if decision.get('remaining_requirements'):
            return owner['item_id']
        return super().preserved_owner(owner, decision)

    def validate_deletion_receipt(self, raw, graph):
        super().validate_deletion_receipt(raw, graph)
        active = {n['item_id'] for n in graph['nodes'] if not n.get('evidence_only')}
        if active and active <= set(raw['drop_ids']) and raw['request'].strip():
            raise ValueError('The receipt drops every active fragment but request is nonempty. '
                'Do not restore previously removed requirements. If no requested contribution '
                'survives, return an empty request. If a current contribution really survives, '
                'retain or revise its actual source ID consistently with the current request '
                'and pending removal; historical evidence-only nodes are not new requirements.')

    def prepare_menu(self, call, session, plan, context, max_options=5):
        # Validate the caller's plan before reserving it for this transaction.
        # Refining scope -> field changes priority but cannot switch questions
        # midway through preparation. No cursor survives into session state.
        self.engine.decision(session, plan)
        previous = self._preparing
        self._preparing = (session['view']['inventory_id'], len(session['turns']), plan['question_id'])
        try:
            updated, public = super().prepare_menu(call, session, plan, context, max_options)
        finally:
            self._preparing = previous
        if public is not None:
            question = self.engine.decision(updated, plan)
            decision = question.get('scoped_permission_decision')
            if decision:
                public = scoped_public_question(public, decision, question['item']['span'])
                updated['pending']['public'] = deepcopy(public)
        return updated, public

    @contextmanager
    def installed(self):
        compact = frozen_module('compact_analysis')
        updates = frozen_module('session_update')
        hierarchical = frozen_module('hierarchical_questions')
        effects = frozen_module('answer_effects')
        prompts = frozen_module('prompts')
        from . import permission_lineage as lineage
        original_effects = lineage.validate_effects
        original_bundle = updates.bundle_questions
        original_ready = hierarchical.ready
        original_commit = self.original['commit_answer']
        def commit_with_identity(call, session, plan, question, answer, context):
            q = self.engine.decision(session, plan)
            result = original_commit(call, session, plan, question, answer, context)
            for turn in result['turns'][len(session['turns']):]:
                turn['target_inventory_id'] = session['view']['inventory_id']
                if q.get('scoped_permission_decision'):
                    turn['scoped_permission_decision'] = deepcopy(q['scoped_permission_decision'])
            return result
        def local_ready(session, question):
            decision = question.get('scoped_permission_decision')
            if decision and question['dimension'] == 'field':
                # The local purpose has its own field menu. Other unanswered
                # or restricted purposes in the same coarse node must not make
                # this question stale; authorized candidates are still filtered
                # by the normal menu builder.
                return 'scope' not in question['item']['missing']
            return original_ready(session, question)
        self.original['commit_answer'] = commit_with_identity
        # The ordinary engine still rebuilds text once. Replace fixed-ID local
        # updating with local refinement, then assemble the validated inventory.
        try:
            with super().installed():
                original_finish = updates.finish_view
                def finish(session, context):
                    info = session['view'].get('segmentation', {}).get('inventory', {}).get('progressive')
                    if info is not None:
                        session['view']['progressive'] = deepcopy(info)
                    result = original_finish(session, context)
                    check_active_state(result)
                    return result
                with (patch.object(compact, 'analyze_flat', self.analyze_flat),
                      patch.object(prompts, 'SESSION_USER_GOLD', prompts.SESSION_USER_GOLD + LOCAL_SCOPE_RULE),
                      patch.object(updates, 'update_view', lambda *a: (None, {'mode': VERSION})),
                      patch.object(updates, 'finish_view', finish),
                      patch.object(hierarchical, 'ready', local_ready),
                      patch.object(effects, 'resolve_permission_choice', lambda *args: None),
                      patch.object(updates, 'bundle_questions', lambda s, c: revision_scoped_bundle(original_bundle, s, c)),
                      patch.object(lineage, 'validate_effects', lambda raw, p: original_effects(normalize_lineage(raw, p), p))):
                    yield self
        finally:
            self.original['commit_answer'] = original_commit

    def _resynchronize(self, call, retry, analyzed, context):
        # Refresh the CURRENT graph, never the pre-answer parent graph or a new
        # f1 namespace. Refresh has no new answer/removal to replay.
        old_base, old_event, old_session = self._base, self._event, self._session
        self._base, self._event = deepcopy(analyzed['view']), {'kind': 'refresh'}
        self._session = analyzed
        retry.pop('progressive_deletion_base', None)
        try:
            return super()._resynchronize(call, retry, analyzed, context)
        finally:
            self._base, self._event, self._session = old_base, old_event, old_session

    def _removal_history(self, session):
        rows = super()._removal_history(session)
        for row, event in zip(rows, session.get('reduction_audit', [])):
            if event.get('scoped_permission_decision'):
                row['scoped_permission_decision'] = deepcopy(event['scoped_permission_decision'])
        return rows

    def next_step(self, session):
        if self._preparing and session.get('view'):
            inventory, turn, key = self._preparing
            if (session['view'].get('inventory_id') == inventory and len(session['turns']) == turn
                    and key not in session['view']['deferred']):
                question = next((q for q in session['view']['questions'] if q['key'] == key), None)
                if question is not None and frozen_module('hierarchical_questions').ready(session, question):
                    return {'action': 'menu', 'turn': turn, 'question_id': key}
        plan = super().next_step(session)
        if (plan['action'] == 'deliver' and plan.get('reason') != 'user_requested_submission'
                and any(n['missing'] for n in session['view']['items'].values())):
            return {'action': 'unresolved', 'reason': 'unrouted_current_decision', 'turn': len(session['turns'])}
        return plan

    def analyze_session(self, call, session, context, reconstruct=None):
        if self._removal_decision is not None and session.get('progressive_deletion_base'):
            session = deepcopy(session)
            session['reduction_audit'][-1]['scoped_permission_decision'] = deepcopy(self._removal_decision)
        old_base, old_event, old_session = self._base, self._event, self._session
        self._session = session
        self._base = session.get('progressive_deletion_base') or session.get('update_base')
        self._event = ({'kind': 'removal', 'receipt': session['reduction_audit'][-1]}
                       if session.get('progressive_deletion_base') else
                       {'kind': 'answer', 'turn': session['turns'][-1]} if self._base else None)
        def protocol_call(stage, prompt, payload):
            if stage.removesuffix('_contract_retry') == 'permission_lineage':
                payload = deepcopy(payload)
                active = payload['input'] if stage.endswith('_contract_retry') else payload
                active['witnesses'] = lineage_witnesses(active)
                prompt = LINEAGE_PROMPT
            return call(stage, prompt, payload)
        try:
            result = super().analyze_session(protocol_call, session, context, reconstruct)
            if result.get('view'):
                info = result['view']['segmentation']['inventory'].get('progressive')
                if info:
                    result['view']['progressive'] = deepcopy(info)
            result.pop('progressive_deletion_base', None)
            check_active_state(result)
            return result
        finally:
            self._base, self._event, self._session = old_base, old_event, old_session

    def _remove(self, call, session, basis, q, trigger, context):
        working = deepcopy(session)
        working['progressive_deletion_base'] = deepcopy(basis['view'])
        previous = self._removal_decision
        self._removal_decision = deepcopy(q.get('scoped_permission_decision'))
        try:
            result = super()._remove(call, working, basis, q, trigger, context)
        finally:
            self._removal_decision = previous
        result.pop('progressive_deletion_base', None)
        if result.get('reduction_audit'):
            result['reduction_audit'][-1]['analysis_mode'] = 'progressive_local_after_deletion'
        if result.get('view') and result['view'].get('update_audit', {}).get('mode') == 'deletion_full':
            result['view']['update_audit']['mode'] = 'progressive_local_after_deletion'
        return result

    def analyze_flat(self, call, payload, context):
        validated = frozen_module('analysis').validated_call
        base, event = self._base, self._event
        request = payload['current_request']
        choices = {}
        if base is None:
            if self.clarity_first:
                def check_coarse(raw):
                    clean, entries = source_choices(raw, 'fragments')
                    neutral = validate_fragments(clean, request)
                    return neutral, {r['item_id']: es for r, es in zip(neutral['fragments'], entries)}
                neutral, choices = validated(call, 'progressive_coarse', COARSE + SOURCE_CHOICES,
                    {'current_request': request}, check_coarse)
            else:
                neutral = validated(call, 'progressive_coarse', COARSE,
                    {'current_request': request}, lambda raw: validate_fragments(raw, request))
            editable = {r['item_id'] for r in neutral['fragments']}
            replacements, history, boundary, kept_edges = {}, {'retired': {}, 'parents': {}}, {}, []
        elif event['kind'] == 'refresh':
            # No semantic event: retain the current leaves/provenance and only
            # re-evaluate their mapping and edges. No parent split is replayed.
            neutral = {'original_text': request, 'fragments': [
                {'item_id': i, 'span': n['span']} for i, n in base['items'].items()]}
            editable = set(base['items'])
            replacements = {i: [i] for i in editable}
            history = {k: deepcopy(base['progressive'][k]) for k in ('retired', 'parents')}
            boundary, kept_edges = {}, []
            if self.clarity_first:
                choices = deepcopy(base['progressive'].get('source_open_choices', {}))
        else:
            if event['kind'] == 'removal':
                receipt = event['receipt']['receipt']
                removed = set(receipt['drop_ids']) & set(base['items'])
                editable = (set(receipt['revise_ids']) | removed) & set(base['items'])
                for seed in tuple(editable):
                    editable.update(frozen_module('session_update').affected_region(base, seed))
            else:
                removed = set()
                editable = set(frozen_module('session_update').affected_region(base, event['turn']['target_id']))
            # Reconstruction can reword an otherwise unchanged boundary phrase.
            # Such a phrase cannot stay a verbatim current-source node. Invalidate
            # it and its consumers explicitly rather than repeatedly failing the
            # merged-frame validator with an impossible read-only quote.
            changed_sources = {i for i, n in base['items'].items() if n['span'] not in request}
            for seed in changed_sources:
                editable.update(frozen_module('session_update').affected_region(base, seed))
            # Shared boundary providers stay read-only; any boundary edge of a
            # split parent must be remapped explicitly in the mapping response.
            refinement_payload = {'current_request': request, 'event': event,
                'editable_fragments': [{'item_id': i, 'span': n['span'], 'meaning': n['requirement']}
                    for i, n in base['items'].items() if i in editable],
                'boundary_fragments': [{'item_id': i, 'span': n['span'], 'meaning': n['requirement']}
                    for i, n in base['items'].items() if i not in editable],
                'removed_ids': sorted(removed)}
            if editable - removed:
                if self.clarity_first:
                    def check_refine(raw):
                        clean, entries = source_choices(raw, 'pieces')
                        result = refine_inventory(clean, base, editable, removed, request)
                        # IDs are allocated in piece order, following the untouched boundary.
                        children = result[0][len(base['items']) - len(editable):]
                        return (*result, {r['item_id']: es for r, es in zip(children, entries)})
                    fragments, replacements, history, choices = validated(call,
                        'progressive_refine', REFINE + SOURCE_CHOICES, refinement_payload, check_refine)
                else:
                    fragments, replacements, history = validated(call, 'progressive_refine', REFINE,
                        refinement_payload, lambda raw: refine_inventory(raw, base, editable, removed, request))
            else:
                # A receipt deleting all editable nodes has a unique structural
                # result. No surviving local text needs a model rewrite.
                fragments, replacements, history = refine_inventory(
                    {'pieces': []}, base, editable, removed, request)
            boundary = {i: n for i, n in base['items'].items() if i not in editable}
            if self.clarity_first:
                choices.update({i: deepcopy(es) for i, es in
                    base['progressive'].get('source_open_choices', {}).items() if i in boundary})
            kept_edges = [{k: e[k] for k in ('source_id', 'target_id', 'kind')}
                          for e in base['relations']['edges'] if
                          e['source_id'] in boundary and e['target_id'] in boundary]
            editable = {r['item_id'] for r in fragments} - set(boundary)
            neutral = {'original_text': request, 'fragments': fragments}
        mapping_payload = {**payload,
            'fragments': [r for r in neutral['fragments'] if r['item_id'] in editable],
            'boundary_nodes': [analysis_row(n) for n in boundary.values()],
            'previous_relations': base['relations'] if base else {'edges': []},
            'parent_replacements': replacements, 'event': event}
        if self.clarity_first:
            mapping_payload['source_open_choices'] = {i: es for i, es in choices.items() if i in editable}
        if self._session:
            mapping_payload['public_conversation'] = self._history(self._session)
            mapping_payload['scoped_answers'] = [
                {'decision': t['scoped_permission_decision'], 'selected_options': t.get('selected_options', [])}
                for t in self._session['turns'] if t.get('scoped_permission_decision')]
        def check(raw):
            raw = normalize_mapping(raw)
            if self.clarity_first:
                raw = preserve_source_choices(raw, {i: es for i, es in choices.items() if i in editable})
            if not isinstance(raw, dict) or set(raw) != {'items', 'edges'} or not isinstance(raw['items'], list) or not isinstance(raw['edges'], list):
                raise ValueError('Expected items and edges arrays')
            if any(not isinstance(r, dict) or not isinstance(r.get('item_id'), str) for r in raw['items']):
                raise ValueError('Each analysis row needs item_id')
            ids = [r['item_id'] for r in raw['items']]
            if len(ids) != len(editable) or set(ids) != editable:
                raise ValueError('Analyze every editable child exactly once; boundary rows are read-only')
            for e in raw['edges']:
                if not isinstance(e, dict) or not isinstance(e.get('source_id'), str) or not isinstance(e.get('target_id'), str):
                    raise ValueError('Expected edges with source_id and target_id')
                if e['source_id'] not in editable and e['target_id'] not in editable:
                    raise ValueError('Cannot edit boundary-to-boundary edges')
            merged = {'items': raw['items'] + [analysis_row(n) for n in boundary.values()],
                      'edges': raw['edges'] + deepcopy(kept_edges)}
            if base:
                for edge in base['relations']['edges']:
                    a, b = edge['source_id'], edge['target_id']
                    if (a in boundary) == (b in boundary):
                        continue
                    left, right = replacements.get(a, [a]), replacements.get(b, [b])
                    if left and right and not any(e.get('source_id') in left and
                            e.get('target_id') in right and e.get('kind') == edge['kind'] for e in raw['edges']):
                        raise ValueError('Retain the surviving boundary relationship for ' + a + ' -> ' + b)
            parsed = validate_mapping(merged, neutral, payload, context)
            # Verify explicit field choices across descendants, not one former
            # parent. Permission checking still runs on every resulting row.
            if event and event['kind'] == 'answer':
                turn = event['turn']
                if turn.get('selection_mode') in ('return_fields', 'operand', 'complete'):
                    selected = {c for o in turn.get('selected_options', []) for c in o['required_columns']}
                    children = set(replacements.get(turn['target_id'], []))
                    deps = {c for r in raw['items'] if r['item_id'] in children
                            and (turn['selection_mode'] != 'return_fields' or r.get('role') == 'output')
                            for c in r['required_columns']}
                    if not selected <= deps:
                        raise ValueError('Accepted fields must survive in the answered parent descendants: ' + ', '.join(sorted(selected - deps)))
            return parsed
        if editable:
            result = validated(call, 'progressive_analysis',
                frozen_module('prompts').FLAT_COMPACT_ANALYZE + MAPPING_RULES + ROLE_CONTRACT + MAP
                    + (CHOICE_MAPPING if self.clarity_first else ''),
                mapping_payload, check)
        else:
            result = check({'items': [], 'edges': []})
        result[4]['progressive'] = {**history, 'replacements': replacements,
            'event_kind': event['kind'] if event else 'initial', 'editable_ids': sorted(editable),
            'kept_ids': sorted(boundary)}
        if self.clarity_first:
            result[4]['progressive']['source_open_choices'] = choices
        return result


def run(call, request, context, db_name, allowed_columns, ask_user, *,
        max_turns=30, episode=None, clarity_first=False):
    """Run the collected method; private requester references stay in ask_user.

    Installation patches the isolated frozen namespace and is process-local.
    Run concurrent sessions in separate processes, as the matrix runner does.
    """
    with MainMethod(clarity_first=clarity_first).installed():
        return frozen_module('harness').run_session(
            call, request, context, db_name, allowed_columns, ask_user,
            max_turns=max_turns, episode=episode if episode is not None else {}, method='main')



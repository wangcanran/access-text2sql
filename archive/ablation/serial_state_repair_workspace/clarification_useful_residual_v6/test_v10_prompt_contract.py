"""Check the final prompt that v10 sends, including inherited prompt layers."""
import json

from baselines.main_deletion_light_answers_v1.runtime.engine.prompts import (
    FLAT_COMPACT_ANALYZE, SESSION_USER_GOLD, SESSION_MENU_NOTE_RULE,
    MENU_COLUMN_FILTER)
from baselines.main_deletion_light_answers_v1.runtime.method import (
    COARSE, SOURCE_CHOICES)
from baselines.main_deletion_light_answers_v1.runtime.method import (
    MAPPING_RULES, ROLE_CONTRACT, MAP)
from clarification_contract_v5.prompts import ANSWER_TYPES, MAPPING_CONTRACT

from .obligations import INITIAL_CONTRACT
from .pool_method import disambiguate_wire_prompt
from .pool_prompts import DECOMPOSE
from .prompts import BINDINGS
from clarification_deferred_v3_2.prompts import ANSWER_UPDATE
from clarification_deferred_v3_2.prompts import INITIAL_QUESTION_POLICY
from clarification_contract_v5.prompts import UPDATE_BINDINGS
from .output_contracts import compile_output


def test_coarse_prompt_example_includes_typed_open_choices():
    prompt = disambiguate_wire_prompt('progressive_coarse', COARSE + SOURCE_CHOICES)
    start = prompt.index('Return only JSON:') + len('Return only JSON:')
    example = json.loads(prompt[start:prompt.index('Copy original_text', start)].strip()[:-1])
    assert example['fragments'][0]['open_choices'][0]['answer_kind'] == 'attribute'
    assert 'Each fragment MUST have open_choices' in prompt
    assert 'Preserve a generic requested difference' in prompt
    assert 'filter, ranking or sign-sensitive result' in prompt


def test_initial_analysis_owns_one_row_per_fragment():
    inherited = (FLAT_COMPACT_ANALYZE + MAPPING_RULES + ROLE_CONTRACT + MAP
                 + ANSWER_TYPES + MAPPING_CONTRACT + INITIAL_CONTRACT)
    prompt = disambiguate_wire_prompt('progressive_analysis', inherited)
    assert 'Return exactly ONE items row for EACH supplied editable fragment' in prompt
    assert "Each row owns ONLY its fragment's contribution" not in prompt
    assert 'Unmapped contributions use false and []' not in prompt
    assert prompt.index('COMPOSITE FRAGMENT WIRE CONTRACT') > prompt.index(
        'PERSISTENT CONTRIBUTION CONTRACT')
    start = prompt.index('Return JSON only:') + len('Return JSON only:')
    example = json.loads(prompt[start:prompt.index('This example represents', start)].strip()[:-1])
    assert set(example) == {'items', 'edges'}
    row = example['items'][0]
    assert row['item_id'] == 'f1' and row['role'] == 'output'
    assert row['answer_kinds'] == {'field': 'attribute'}
    assert row['binding_claims'][0]['state'] == 'open'
    assert row['grounded'] is False
    assert 'generic returned difference can remain' in prompt
    assert 'Each other fragment owns its own dependencies' in prompt
    assert 'For each question_focus entry, put its answer_kind' in prompt
    assert 'offer the relevant participating measures as field choices' not in prompt
    assert 'ask ONE operation question' in prompt


def test_analysis_prompt_with_all_inherited_layers_keeps_one_question_policy():
    inherited = (FLAT_COMPACT_ANALYZE + MAPPING_RULES + ROLE_CONTRACT + MAP
                 + INITIAL_QUESTION_POLICY + ANSWER_TYPES + MAPPING_CONTRACT
                 + INITIAL_CONTRACT)
    prompt = disambiguate_wire_prompt('progressive_analysis', inherited)
    assert 'generic requested summary/statistic is ONE operation question' in prompt
    assert 'ask ONE operation question' in prompt
    assert 'offer the relevant participating measures as field choices' not in prompt
    assert 'Each open_choices entry additionally has answer_kind' not in prompt


def test_requester_optional_note_prompt_has_consistent_choose_format():
    inherited = SESSION_USER_GOLD.replace(
        'answer is nonempty only\nfor answer.',
        'answer is nonempty for answer; for choose it may contain an optional note.'
    ) + SESSION_MENU_NOTE_RULE
    prompt = disambiguate_wire_prompt('session_user_gold', inherited)
    assert 'allow_note=true' in prompt
    assert 'For choose, answer="" and selected_ids must be nonempty' not in prompt
    assert 'A menu selection of "None of these meets my requirement"' in prompt


def test_field_filter_prompt_describes_pre_filtered_permission_domain():
    prompt = disambiguate_wire_prompt('hierarchical_field_filter', MENU_COLUMN_FILTER)
    assert 'candidate_domain may already be permission-filtered' in prompt
    assert 'Permission filtering is performed by the runner after this selection.' not in prompt
    assert 'candidate_domain as the only possible field IDs' in prompt


def test_grouping_prompt_matches_partition_validator():
    from baselines.main_deletion_light_answers_v1.runtime.engine.prompts import STRICT_CHOICE_GROUPS
    prompt = disambiguate_wire_prompt('hierarchical_menu_groups', STRICT_CHOICE_GROUPS)
    assert '2 and max_options groups' in prompt
    assert 'choice_ids' in prompt
    assert 'partitioning every supplied choice_id exactly once' in prompt
    assert 'one option per field' in prompt


def test_menu_callback_rewrites_imported_field_filter_alias():
    from unittest.mock import patch
    from .pool_method import PoolMethod

    observed = []
    def fake_prepare(self, call, session, plan, context, max_options):
        call('hierarchical_field_filter', MENU_COLUMN_FILTER, {})
        return session, {'question': 'Which field?'}
    with patch('clarification_useful_residual_v6.method.UsefulResidualMethod.prepare_menu',
               fake_prepare):
        PoolMethod().prepare_menu(lambda stage, prompt, payload:
            observed.append((stage, prompt)), {}, {}, {}, 5)
    assert observed and 'candidate_domain may already be permission-filtered' in observed[0][1]


def test_simulated_requester_has_explicit_none_wire_example():
    prompt = disambiguate_wire_prompt('session_user_gold', SESSION_USER_GOLD)
    assert 'If none matches the reference intention use unanswerable.' not in prompt
    assert 'still needs module_ids' in prompt
    assert '"module_ids":["o2"]' in prompt
    assert 'optional menu-note rule below' not in prompt


def test_live_adapter_applies_prompt_rules_after_pool_contract():
    import pytest
    from clarification_deferred_deletion.baseline import analysis
    from .pool_method import PoolMethod

    observed = []
    def capture(stage, prompt, payload):
        observed.append((stage, prompt))
        return {}

    inherited = (FLAT_COMPACT_ANALYZE + MAPPING_RULES + ROLE_CONTRACT + MAP
                 + ANSWER_TYPES + MAPPING_CONTRACT)
    with PoolMethod().installed():
        with pytest.raises((ValueError, KeyError, TypeError)) as error:
            analysis.validated_call(capture, 'progressive_analysis',
                                    inherited, {}, lambda raw: raw)
    assert observed, str(error.value)
    assert 'PERSISTENT CONTRIBUTION CONTRACT' in observed[0][1]
    assert observed[0][1].rfind('COMPOSITE FRAGMENT WIRE CONTRACT') > observed[0][1].rfind(
        'PERSISTENT CONTRIBUTION CONTRACT')


def test_decomposition_has_single_wire_format_and_typed_edges():
    start = DECOMPOSE.index('Return JSON only, exactly:') + len('Return JSON only, exactly:')
    end = DECOMPOSE.index('Every node has exactly', start)
    sample = json.loads(DECOMPOSE[start:end].strip())
    assert set(sample) == {'nodes', 'retired_dependencies'}
    assert set(sample['nodes'][0]['binding_claims'][0]) == {
        'meaning', 'columns', 'use', 'state', 'obligation_ids'}
    assert '[output,subject_leaf,"requires"]' not in BINDINGS
    assert 'Optional filters use applies_to' not in BINDINGS
    assert 'Do not attach the outpatient leaf with applies_to' not in BINDINGS
    assert 'Code compiles use and state from the node role/kind' not in BINDINGS
    assert 'Every claim explicitly supplies meaning' in BINDINGS
    assert 'if state is omitted' not in DECOMPOSE
    assert 'optional qualification' not in BINDINGS
    assert 'The seed leaf may own only the asked IDs' not in BINDINGS
    assert 'pending field/value/operation questions may stay' in BINDINGS
    assert 'business qualification' in BINDINGS
    assert 'The exact keys of parents are the complete required parent set' in DECOMPOSE
    assert 'physical ID only as the JSON key' in DECOMPOSE
    assert 'never repeat the table.column ID as the label' in DECOMPOSE
    assert 'Use decision_ids ONLY from pending_decisions' in DECOMPOSE
    assert 'For every column in a parent' in DECOMPOSE


def test_transaction_prompt_example_is_compilable_and_owns_claims():
    prompt = disambiguate_wire_prompt('semantic_transaction',
                                      ANSWER_UPDATE + UPDATE_BINDINGS)
    start = prompt.index('Return JSON only:') + len('Return JSON only:')
    example = json.loads(prompt[start:prompt.index('Return the complete transaction', start)].strip()[:-1])
    transaction = compile_output('semantic_transaction', example).to_legacy()
    assert set(transaction) >= {'updates', 'edges', 'retire_columns',
                                'question_changes', 'settled_decisions'}
    claim = transaction['updates']['f1'][0]['binding_claims'][0]
    assert claim['obligation_ids'] == ['o1']
    assert 'Only updates is required.' not in prompt
    assert 'A generic difference does not establish an absolute value' in prompt
    assert 'Edges are a local delta' in prompt
    assert 'Preserve attachments\nto untouched nodes' not in prompt


def test_prompt_rewrites_fail_closed_when_an_inherited_contract_changes():
    import pytest
    with pytest.raises(ValueError, match='inherited prompt changed'):
        disambiguate_wire_prompt('progressive_coarse', 'unrecognized inherited prompt')
    with pytest.raises(ValueError, match='inherited prompt changed'):
        disambiguate_wire_prompt('session_user_gold', 'unrecognized inherited prompt')
    with pytest.raises(ValueError, match='inherited prompt changed'):
        disambiguate_wire_prompt('semantic_transaction', 'unrecognized inherited prompt')

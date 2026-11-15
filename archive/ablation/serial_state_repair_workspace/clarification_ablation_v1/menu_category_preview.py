"""Expose existing authorized leaf labels in category navigation."""
from copy import deepcopy

from .candidate_context import SchemaAlignedMenuMethod

VERSION = 'main_v10_menu_category_preview_v1'


def category_labels(groups, leaves):
    by_id = {c['choice_id']: c['text'] for c in leaves}
    return [g['label'].strip() + ': ' + '; '.join(by_id[c] for c in g['choice_ids'])
            for g in groups]


def preview_menu(state, public):
    pending = state.get('pending')
    if not pending or not pending.get('category'):
        return state, public
    state, public = deepcopy(state), deepcopy(public)
    pending = state['pending']
    frontier = state['menu_frontiers'][pending['decision_key']]
    groups = [dict(label=o['text'], choice_ids=o['choice_ids']) for o in pending['options']]
    labels = category_labels(groups, frontier['leaves'])
    by_id = {}
    for option, label in zip(pending['options'], labels):
        option['text'] = label
        by_id[option['id']] = label
    for option in public['options']:
        if option['id'] in by_id:
            option['label'] = by_id[option['id']]
    pending['public'] = deepcopy(public)
    return state, public


class CategoryPreviewMenuMethod(SchemaAlignedMenuMethod):
    def prepare_menu(self, call, session, plan, context, max_options=5):
        state, public = super().prepare_menu(call, session, plan, context, max_options)
        # Do not append a preview twice when returning an existing pending menu.
        if (state.get('pending') or {}).get('category_preview'):
            return state, public
        state, public = preview_menu(state, public)
        if state.get('pending') and state['pending'].get('category'):
            state['pending']['category_preview'] = True
        return state, public

    def audit(self):
        return dict(super().audit(),version=VERSION,variant='menu_category_preview_v1',
                    category_preview='all_existing_authorized_leaf_labels')

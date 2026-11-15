"""Fixed, manually annotated semantic inventories; annotations never reach API.

These are controlled dependency tests, NOT an end-to-end extraction benchmark.
The school/finance/hero inputs come from saved cases in the specified 330 set.
Their withdrawal sequences are interventions, not replayed user choices.
"""
from pathlib import Path
import json


def case(name, request, atoms, roots, requires, steps, source='constructed'):
    return dict(name=name, request=request, atoms=atoms, expected={
        'roots': roots, 'requires': [list(x) for x in requires]},
        steps=[{'withdraw': [target], 'meaning': atoms[target]} for target in steps],
        source=source)


def cases():
    result = [
        case('shared_local',
             'For accounts opened in 2020, return account identifiers and the minimum '
             'and maximum balances computed from transactions before the loan.',
             {'accounts': 'Account population', 'opened': 'Account opening year is 2020',
              'id': 'Return account identifiers', 'minimum': 'Return minimum balance',
              'maximum': 'Return maximum balance', 'balance': 'Transaction balance operand',
              'preloan': 'Use only transactions before the loan for balance calculations',
              'binding': 'Bind each account to its loan and transactions'},
             ['id', 'minimum', 'maximum', 'opened'],
             [('id', 'accounts'), ('opened', 'accounts'), ('minimum', 'balance'),
              ('maximum', 'balance'), ('minimum', 'preloan'), ('maximum', 'preloan'),
              ('balance', 'binding'), ('preloan', 'binding'), ('binding', 'accounts')],
             ['minimum', 'maximum']),
        case('independent_existence',
             'For accounts opened in 2020 that have at least one transaction before '
             'their loan, return account identifiers and the minimum and maximum '
             'balances over those pre-loan transactions.',
             {'accounts': 'Account population', 'opened': 'Account opening year is 2020',
              'exists': 'Require at least one transaction before the account loan',
              'id': 'Return account identifiers', 'minimum': 'Return minimum balance',
              'maximum': 'Return maximum balance', 'balance': 'Transaction balance operand',
              'preloan': 'Use pre-loan transactions in the balance calculation',
              'binding': 'Bind accounts, loans and transactions'},
             ['id', 'minimum', 'maximum', 'opened', 'exists'],
             [('id', 'accounts'), ('opened', 'accounts'), ('exists', 'binding'),
              ('minimum', 'balance'), ('maximum', 'balance'), ('minimum', 'preloan'),
              ('maximum', 'preloan'), ('balance', 'binding'), ('preloan', 'binding'),
              ('binding', 'accounts')], ['minimum', 'maximum']),
        case('filter_role_school',
             'Give information about the schools with a K-12 student-related proportion '
             'above a specified positive threshold and test takers who meet or exceed '
             'a specified performance criterion?',
             {'schools': 'School population', 'info': 'Return unspecified school information',
              'proportion': 'School K-12 proportion exceeds an unspecified positive threshold',
              'performance': 'The school has test takers meeting or exceeding an unspecified criterion'},
             ['info', 'proportion', 'performance'],
             [('info', 'schools'), ('proportion', 'schools'), ('performance', 'schools')],
             ['proportion', 'performance'], 'bird330:24:level0'),
        case('client_binding_finance',
             'For the client associated with a specified loan application, provide a '
             'comprehensive profile including their birthday, age at the time of loan '
             'application, location, financial activity prior to the loan, a quantitative '
             'financial ratio, balance range, information about relevant payment or '
             'credit accounts, and a summary of prior borrowing activity.',
             {'client': 'Client population', 'loan_binding': 'The client associated with a loan application',
              'specified': 'Restrict to the specified, still unidentified loan application',
              'birthday': 'Return client birthday', 'age': 'Return age at loan application',
              'location': 'Return client location', 'activity': 'Return financial activity prior to loan',
              'ratio': 'Return an unspecified financial ratio', 'range': 'Return an unspecified balance range',
              'cards': 'Return information about relevant payment or credit accounts',
              'borrowing': 'Return summary of prior borrowing activity'},
             ['specified', 'birthday', 'age', 'location', 'activity', 'ratio', 'range', 'cards', 'borrowing'],
             [('loan_binding', 'client'), ('specified', 'loan_binding')]
             + [(x, 'loan_binding') for x in ['birthday', 'age', 'location', 'activity', 'ratio', 'range', 'cards', 'borrowing']],
             ['birthday', 'specified', 'location'], 'bird330:113:level1'),
        case('hero_shared_scope',
             "State all of 3-D Man's attributes along with their values.",
             {'hero': 'Hero is 3-D Man', 'all': 'Include all attributes of that hero',
              'attributes': 'Return attribute names', 'values': 'Return corresponding attribute values',
              'binding': 'Associate that hero with its attributes'},
             ['attributes', 'values', 'all'],
             [('attributes', 'binding'), ('values', 'binding'), ('all', 'binding'), ('binding', 'hero')],
             ['values'], 'bird330:796:reference'),
        case('local_vs_population',
             'For schools in California, return school names, the count of students '
             'whose score is at least 1500, and their average score. The score '
             'condition applies only to these two calculations, not to which schools are returned.',
             {'schools': 'School population', 'region': 'School is in California',
              'name': 'Return school name', 'count': 'Return count of qualifying students',
              'average': 'Return average score of qualifying students',
              'score_filter': 'Use students with score >= 1500 in the two calculations',
              'students': 'Associate each school with its students', 'score': 'Student score operand'},
             ['region', 'name', 'count', 'average'],
             [('region', 'schools'), ('name', 'schools'), ('count', 'score_filter'),
              ('average', 'score_filter'), ('average', 'score'),
              ('score_filter', 'score'), ('score', 'students'), ('students', 'schools')],
             ['count', 'average']),
        case('independent_group_order',
             'For each district, return district name, school count and average score. '
             'Group by district and order districts by school count descending.',
             {'districts': 'District population', 'name': 'Return district name',
              'count': 'Return school count', 'average': 'Return average score',
              'count_calc': 'Calculate school count per district',
              'average_calc': 'Calculate average score per district',
              'binding': 'Associate schools and scores with districts',
              'group': 'Group the result by district',
              'order': 'Order districts by school count descending'},
             ['name', 'count', 'average', 'group', 'order'],
             [('name', 'districts'), ('count', 'count_calc'), ('average', 'average_calc'),
              ('count_calc', 'binding'), ('average_calc', 'binding'),
              ('binding', 'districts'), ('group', 'districts'), ('order', 'count_calc')],
             ['count', 'average']),
        case('boolean_scope',
             'Return names and websites of schools satisfying either a free-meal '
             'proportion above 0.1 or a SAT criterion of at least 1500, excluding closed schools.',
             {'schools': 'School population', 'name': 'Return school name',
              'website': 'Return school website',
              'either': 'School satisfies (free-meal proportion > 0.1 OR SAT criterion >= 1500)',
              'not_closed': 'Exclude closed schools'},
             ['name', 'website', 'either', 'not_closed'],
             [('name', 'schools'), ('website', 'schools'), ('either', 'schools'), ('not_closed', 'schools')],
             ['website', 'either']),
    ]
    # Prevent accidental drift of the saved real inputs. No gold SQL is loaded.
    root = Path(__file__).resolve().parents[3]
    folder = root / 'artifacts/clarification_main/20261021_decision_update_light_v11/light'
    for c in result:
        if c['source'] in ('bird330:24:level0', 'bird330:113:level1'):
            q, level = (24, 0) if ':24:' in c['source'] else (113, 1)
            saved = json.loads((folder / f'{q}_{level}_reduction.json').read_text(encoding='utf8'))
            if c['request'] != saved['request']:
                raise ValueError('Saved request differs from experiment fixture')
    return result

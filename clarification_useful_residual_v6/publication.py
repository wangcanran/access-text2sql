"""One user-text boundary; internal bindings and option identities stay intact."""
from copy import deepcopy
import re


PUBLIC_WORDING = '''Rewrite ONLY the user-facing wording in the supplied JSON public question.
Return exactly {"public": <the same public-question structure>} as JSON.
Keep every key, option ID, option order, selection mode and other metadata.
Change only question, focus and option label strings. Preserve ALL business
meanings, literal values, distinctions and constraints. Use ordinary business
language, never physical table/column identifiers, handles, SQL or schema paths.
Remove schema annotations such as "Consumption (yearmonth.Consumption)" while
retaining "Consumption". Explain a technical term in business language when
necessary; do not replace distinct options with the same vague label. Do not
add fields, examples, alternatives, permission explanations or new questions.
This is presentation only: it cannot authorize or change candidate bindings.
'''


def identifier_patterns(context):
    patterns = []
    for table in context.get('tables', []):
        t = table['name']
        for column in table['columns']:
            c = column['name']
            # SQL quoting and whitespace cannot evade a qualified-name check.
            atom = lambda s: r'[\[\]`"\s]*' + re.escape(s) + r'[\[\]`"\s]*'
            patterns.append(re.compile(r'(?<!\w)' + atom(t) + r'\.' + atom(c) + r'(?!\w)', re.I))
        for name in [t] + [c['name'] for c in table['columns']]:
            patterns.append(re.compile(r'(?:`' + re.escape(name) + r'`|\[' + re.escape(name) + r'\])', re.I))
            # Plain English words can also be legitimate business language.
            if '_' in name or re.search(r'[a-z][A-Z]', name):
                patterns.append(re.compile(r'(?<!\w)' + re.escape(name) + r'(?!\w)', re.I))
    return patterns


def public_strings(public):
    for key in ('question', 'focus'):
        if key in public:
            yield public[key]
    for option in public.get('options', []):
        yield option['label']


def exposed_identifiers(public, context):
    patterns = identifier_patterns(context)
    return [s for s in public_strings(public) if any(p.search(s) for p in patterns)]


def validate_public_wording(raw, original, context):
    if not isinstance(raw, dict) or set(raw) != {'public'}:
        raise ValueError('Return exactly {public: same question structure}')
    result = raw['public']
    if not isinstance(result, dict) or set(result) != set(original):
        raise ValueError('Public question keys must be unchanged')
    masked_old, masked_new = deepcopy(original), deepcopy(result)
    for value in (masked_old, masked_new):
        for key in ('question', 'focus'):
            if key in value:
                if not isinstance(value[key], str) or not value[key].strip():
                    raise ValueError('Question wording must be nonempty text')
                value[key] = '<wording>'
        if not isinstance(value.get('options', []), list):
            raise ValueError('Options must remain a list')
        for option in value.get('options', []):
            if not isinstance(option, dict) or not isinstance(option.get('label'), str) or not option['label'].strip():
                raise ValueError('Every option needs a nonempty business label')
            option['label'] = '<wording>'
    if masked_old != masked_new:
        raise ValueError('Presentation cannot change option IDs/order or metadata')
    labels = [o['label'].casefold().strip() for o in result.get('options', [])]
    if len(labels) != len(set(labels)):
        raise ValueError('Distinct options require distinct business labels')
    if exposed_identifiers(result, context):
        raise ValueError('Physical schema identifiers remain in public wording')
    return deepcopy(result)

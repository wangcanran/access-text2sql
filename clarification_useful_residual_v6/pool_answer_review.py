"""Local answer coverage check: new answer facts must become commitments."""
from copy import deepcopy

PROMPT = '''Check ONLY this local user-answer replacement, not global completion.
Return JSON exactly {"accepted":true,"issues":[]} or
{"accepted":false,"issues":[{"code":"missing_intent|contradictory_intent|missing_dependency","quote":"exact substring of answer_text",
"reason":"specific missed or changed contribution"}]}.
The user answer settles its asked business question, but may introduce other
known conditions, attributes or calculation detail. Check that every material
fact stated in the answer and retained in requirement text is represented by
the COLLECTIVE business meanings of binding_claims. One fact may be expressed
jointly by a count claim and a cohort claim; no separate definition claim is
needed. Do not require one claim for every phrase. Claim IDs and decision
lifecycle are assigned and checked by code, outside this review.
The answer supplies clarification, NOT result rows. Never demand that the user
provide the requested query results (e.g. identifiers) when answering a locator
question. Check the answer facts in the replacement, not vice versa.
An answer may provide both an identifier and a requested attribute, or use an
identifier to locate the requested entity. Do not call that a changed intent
merely because the answer mentions the locator field. Judge the full question,
prior selected options and replacement together. The payload's interaction_context
contains prior_interactions for the same node lineage and asked_interaction for
the current question, selected options and answer; use those records as the
authoritative interaction evidence. Do not infer a selection that is absent.
Every explicit population qualifier is material, including geography,
membership, cohort or status embedded in parentheses. A qualifier restricting
which entities are counted must appear in the collective claims, in addition
to the distinct-count operation. Treat a qualifier as an illustration only
when the answer clearly says it does not constrain the requested set.
Check two independent things: (1) collective INTENT coverage; (2) missing schema
operands for answer facts claimed as bound. A bound flag is a proposal, NOT proof.
Do not substitute an alternative implementation or reject equivalent physical
representations. For a genuinely open claim, missing grounding is not an error:
A condition claim in state=open IS recorded: the pool handles its binding later.
For a bound claim, ensure all necessary answer predicates and relationships
have schema operands somewhere in the collective claims. Merely saying a
category or a relationship in meaning does not authorize its implementation.
Example: 'count inactive accounts' with account ID only misses the status-field
operand even if the count claim says 'inactive'. A separate open inactive-filter
claim is valid; an ungrounded bound inactive-filter claim is not.
Likewise selecting related entities needs the specific association endpoints,
not only the two entity identifiers or a shared higher-level grouping key.
Use missing_dependency ONLY for a concrete necessary operand missing collectively
from bound claims; explain the absent field/relationship against the schema.
If a literal time/value claim has empty columns but another bound claim for the
same predicate already supplies its date, key, or relationship field, treat the
collective grounding as sufficient and do not report missing_dependency.
Equivalent units, synonyms, formula rewrites and numerical formats are valid:
120 seconds, 02:00 and 120000 milliseconds describe the same limit; counting IDs
can implement counting entities. Never demand an extra column for a literal.
Example: answer='Count inactive accounts'; claims=['Count accounts',
'Filter to inactive accounts (open)'] => accept. A count claim plus this filter
also covers the definition of accounts being counted. If claims only say
'Count all accounts' while only output prose mentions inactive => reject the
missing inactive cohort. Do not reject merely because schema grounding is open.
Do not reopen answered choices, invent extra requirements, inspect permissions,
or demand implementation detail from the user. Code schedules internal binding
and authorization afterwards, independently of this semantic coverage check.
When an answer is more specific than the wording of the displayed question,
do not call that a contradiction merely because it selects a concrete value.
Inspect only the supplied answer, interaction context, replacements, and schema.
This payload does not include the authoritative pending-decision ledger: do not
infer whether a decision was settled. Reject only a concrete omission, change,
or bound-claim dependency gap visible in this payload, quoting exact answer words.
This is not SQL execution or an invitation to search alternative implementations.
'''


def validate(raw, answer_text):
    if (not isinstance(raw, dict) or set(raw) != {'accepted', 'issues'}
            or type(raw['accepted']) is not bool or not isinstance(raw['issues'], list)
            or raw['accepted'] != (not raw['issues'])):
        raise ValueError('Expected accepted boolean and consistent issues array')
    for issue in raw['issues']:
        if (not isinstance(issue, dict) or set(issue) != {'code', 'quote', 'reason'}
                or issue['code'] not in ('missing_intent', 'contradictory_intent', 'missing_dependency')
                or not isinstance(issue['quote'], str) or not issue['quote'].strip()
                or issue['quote'] not in answer_text
                or not isinstance(issue['reason'], str) or not issue['reason'].strip()):
            raise ValueError('Answer coverage issue must quote the actual answer')
    return deepcopy(raw)

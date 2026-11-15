from .gap_selection import TYPE_ORDER

UNKNOWN_ANNOTATION_PROMPT = r'''Classify every unresolved database-query item in context so a deterministic
runner can choose what to ask next. The input may mix incomplete request fragments
and clear requests that cannot be used under the current role. Classify the business
choice the user must make, not the item's source or its outputs/modifiers bucket.
Do not detect, remove, answer, rewrite or merge items. Do not inspect permissions,
generate options, choose priority, or decide completion.

Return exactly one JSON object and one annotation for every supplied followup_id:
{"annotations":[{"followup_id":"mapping:o1","type":"output","reason":"The requested returned value needs an accessible alternative"}]}

Types:
subject: which entity or business object is queried, or an unresolved referent that
determines it. An unknown identifier value is condition, not subject.
output: which returned fields or business measures are requested.
calculation: counting unit, aggregation definition, denominator or grouping unit.
condition: filtering attribute, operator or comparison value.
scope: ranking/order, population range or result count.
other: no single type fits reliably, including inseparable mixed choices.

For a clear-but-inaccessible item, classify the semantic role of the requested
meaning (for example, an inaccessible requested return value is output; an
inaccessible filter is condition). For a gap, classify the missing choice described
by that gap. Use the full request to disambiguate. Keep the reason short and business
focused. Copy every followup_id exactly. Allowed types: ''' + ", ".join(TYPE_ORDER)

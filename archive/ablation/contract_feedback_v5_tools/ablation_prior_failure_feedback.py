"""Carry only this request's public contract errors into a fresh retry.

No previous answer, Gold SQL, private simulator input or evaluation label is
copied. Existing validators and model responses remain untouched.
"""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch

VERSION = 'prior_contract_errors_v1'


@contextmanager
def installed(recorder_class, errors):
    original = recorder_class.bind

    def bind(self, record, *args, **kwargs):
        call = original(self, record, *args, **kwargs)
        key = str(record['question_id']) + '_' + str(record['level'])
        previous = errors.get(key)

        def invoke(stage, prompt, payload):
            if previous and stage in ('progressive_coarse', 'progressive_analysis'):
                payload = deepcopy(payload)
                payload['previous_attempt_contract_failure'] = dict(
                    error=previous,
                    instruction='This same request previously failed the stated existing contract. '
                    'Reconsider its declarations using the actual request and available schema. '
                    'Framing prose is not an independent database result or an entity locator. '
                    'Preserve all actual requested results, conditions, confirmed choices and dependencies. '
                    'This is an error diagnostic, not an intended answer or authority to relax validation.')
            return call(stage, prompt, payload)
        return invoke

    with patch.object(recorder_class, 'bind', bind):
        yield

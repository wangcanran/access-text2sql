"""Bounded additional public-error correction; the original validator decides."""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch
from clarification_deferred_deletion.baseline import analysis
from clarification_useful_residual_v6 import output_contracts

VERSION = 'bounded_output_validation_recovery_v2'

@contextmanager
def installed(extra_attempts=4):
    original = output_contracts.validated_output_call
    def validated(call, stage, prompt, payload, validator):
        state = {}
        def observe(actual_stage, actual_prompt, actual_payload):
            raw = call(actual_stage, actual_prompt, actual_payload)
            state.update(raw=deepcopy(raw), stage=actual_stage, prompt=actual_prompt, payload=deepcopy(actual_payload))
            return raw
        try:
            return original(observe, stage, prompt, payload, validator)
        except (ValueError, TypeError, KeyError) as error:
            if stage == 'session_user_gold' or not state:
                raise
            last_error = error
            for index in range(extra_attempts):
                previous_payload = state['payload']
                current_input = previous_payload.get('input', payload) if isinstance(previous_payload, dict) else payload
                feedback = dict(input=deepcopy(current_input), previous_response=deepcopy(state['raw']),
                                contract_error=str(last_error), recovery_attempt=index+1,
                                instruction='Return the complete corrected JSON under the existing contract. Preserve current editable ownership, pending decision IDs, inherited dependencies and contributions. Do not invent answers or weaken validation.')
                raw = observe(stage.removesuffix('_contract_retry')+'_contract_retry', state['prompt'], feedback)
                try:
                    compiled = output_contracts.compile_output(stage, raw)
                    return validator(compiled.to_legacy())
                except (ValueError, TypeError, KeyError) as next_error:
                    last_error = next_error
            raise last_error
    with patch.object(output_contracts, 'validated_output_call', validated):
        yield

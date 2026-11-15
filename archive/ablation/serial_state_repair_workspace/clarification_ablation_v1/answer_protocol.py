"""Separate requester premise correction from method withdrawal handling."""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch
from .open_questions import OpenQuestionMethod
from clarification_deferred_deletion.baseline import module
from clarification_useful_residual_v6.pool_method import PoolMethod

VERSION='open_answer_protocol_probe_v1'
CORRECTION_RULE='''
PREMISE CORRECTION: You may correct a mistaken premise in the current question.
If private_original and its relevant source modules establish that the requested
scope is already described by a grouping/entity/context, explain that meaning
instead of inventing an additional filter. For example, a request grouped by an
activity does not by itself request a specific activity value. You can answer
that the grouping is intended without an extra value restriction when supported
by the original intention. Cite the relevant supplied module_ids and use the
existing action=answer wire format. This is an answer to the current question,
not withdrawal of any output or condition.
Absence of a WHERE clause alone is NOT evidence that all scope is unrestricted.
Do not invent defaults, missing thresholds or parameters. When genuinely unable
to determine the requested detail, use unanswerable. Never invent an answer just
to avoid unanswerable. Keep necessary existing conditions and exclusions. SQL
remains supplementary and cannot override the original intention. No SQL,
physical field names or permission information in the public reply.
For a menu, the original offered-ID contract takes precedence: select only
offered choices consistent with your intention; never emit a free-text answer
or invent an option to correct a menu. If correction cannot be expressed in
the offered choices, use the existing None-of-these/unanswerable protocol.
'''
UPDATE_RULE='''
An answer may correct an unnecessary premise in the asked clarification. Treat
such a public correction as evidence about that local decision only: settle an
invented extra restriction without deleting the requested output or other
established predicates. Do not reinterpret a correction as user withdrawal.
Bind actual stated content and run all existing permission/dependency checks.
Do not assume that a field is authorized because the user named it.
'''

class AnswerProtocolMixin:
    def __init__(self,*,correct_user=False,keep_unknown=False):
        super().__init__();self.correct_user=correct_user;self.keep_unknown=keep_unknown

    def audit(self):
        parent=getattr(super(),'audit',lambda:dict(version='main_v10_menu_answer_protocol_v1',variant='all_open',all_questions_open=False,menu_mechanism_unchanged=True))
        return dict(parent(),answer_protocol=VERSION,
                    requester_premise_correction=self.correct_user,
                    unanswerable_is_not_withdrawal=self.keep_unknown)

    def commit_answer(self,call,session,plan,question,answer,context):
        if self.keep_unknown and answer['action']=='unanswerable':
            # Bypass only the inherited unanswerable->_remove conversion;
            # use the same guarded recorder/identity boundary for the turn.
            result=self.original['commit_answer'](call,session,plan,question,answer,context)
            result['contract_unresolved_reason']='User cannot determine the asked detail; no withdrawal or deletion authorized'
            result.setdefault('answer_protocol_audit',[]).append(dict(
                question_id=plan['question_id'],action='preserve_unresolved',pruning_authorized=False))
            return result
        return super().commit_answer(call,session,plan,question,answer,context)

    def _transaction(self,call,session,base,event,context):
        def adapted(stage,prompt,payload):
            if self.correct_user and stage.removesuffix('_contract_retry')=='semantic_transaction':
                prompt+='\n'+UPDATE_RULE
            return call(stage,prompt,payload)
        return super()._transaction(adapted,session,base,event,context)

    @contextmanager
    def installed(self):
        with super().installed():
            prompts=module('engine.prompts')
            if self.correct_user:
                with patch.object(prompts,'SESSION_USER_GOLD',prompts.SESSION_USER_GOLD+'\n'+CORRECTION_RULE):
                    yield self
            else:yield self

class AnswerProtocolMethod(AnswerProtocolMixin,OpenQuestionMethod):
    pass

class MenuAnswerProtocolMethod(AnswerProtocolMixin,PoolMethod):
    """Original menu construction/navigation/authorization, revised answers only."""
    pass

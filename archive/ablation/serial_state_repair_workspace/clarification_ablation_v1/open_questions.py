"""Opt-in all-open interaction on the frozen v10 permission/state backbone."""
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch
from clarification_useful_residual_v6.pool_method import PoolMethod, _PATCH_LOCK

VERSION='main_v10_all_open_questions_v2'

class OpenQuestionMethod(PoolMethod):
    def __init__(self):
        super().__init__()
        self.open_events=[]
        self._open_public={}

    def next_step(self, session):
        plan=super().next_step(session)
        if plan['action']=='menu':
            return dict(plan,action='ask')
        return plan

    def open_question(self,session,plan):
        q=self.engine.decision(session,plan)
        key=(session['view']['inventory_id'],len(session['turns']),plan['question_id'])
        if key in self._open_public:return deepcopy(self._open_public[key])
        public=dict(question='Regarding "'+q['item']['span']+'": '+q['focus'],
                    focus=q['focus'],options=[],multi_select=False)
        if q.get('open_bundle'):public['requested_dimensions']=q['requested_dimensions']
        public=self._public_text(self._publication_call,public,self._context)
        self._open_public[key]=deepcopy(public)
        return public

    def prepare_menu(self,*args,**kwargs):
        raise AssertionError('All-open scheduler must never prepare a menu')

    def audit(self):
        return dict(version=VERSION,variant='all_open',all_questions_open=True,
                    permission_checks=True,dependency_propagation=True,terminal_gates=True,
                    events=self.open_events)

    @contextmanager
    def installed(self):
        with _PATCH_LOCK,super().installed():
            previous=self.original['commit_answer']
            def commit_open(call,session,plan,question,answer,context):
                self.engine.decision(session,plan)
                if question.get('options') or answer.get('choices'):
                    raise ValueError('All-open protocol forbids options and choice IDs')
                if answer.get('action') not in ('answer','unanswerable','refuse','submit'):
                    raise ValueError('Unexpected open answer action')
                work=deepcopy(session)
                assert not work.get('pending'),'Unexpected prepared menu'
                # Disable only the legacy menu-required wire guard. Restore the
                # strict session setting before semantic updates/authorization.
                strict=work.get('strict_menus',False)
                work['strict_menus']=False
                scheduler=self.engine.next_step
                # strict_menus also influences scheduler priority. The already
                # validated/shown decision must remain reserved during wire
                # validation; resume the real scheduler after committing.
                with patch.object(self.engine,'next_step',lambda s:deepcopy(plan) if s is work else scheduler(s)):
                    result=previous(call,work,plan,question,answer,context)
                result['strict_menus']=strict
                if strict:
                    result.setdefault('public_interactions',[]).append(
                        dict(question=deepcopy(question),answer=deepcopy(answer)))
                    if answer['action']=='answer':result['menu_frontiers']={}
                self.open_events.append(dict(question_id=plan['question_id'],
                    action=answer['action'],turn=len(result['turns'])))
                return result
            self.original['commit_answer']=commit_open
            try:
                with patch.object(self.engine,'prepare_field_menu',lambda session,plan,context,*a,**k:(session,None)), \
                     patch.object(self.engine,'public_question',self.open_question):
                    yield self
            finally:self.original['commit_answer']=previous

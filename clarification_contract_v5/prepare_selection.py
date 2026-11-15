"""Prepare a fixed new sample without inspecting live outcomes."""
import hashlib, json, random
from pathlib import Path
from clarification_deferred_deletion.baseline import ROOT, module

IDS = [(59,0),(131,0),(227,0),(365,0),(879,1),(1041,0),(1369,0),(1489,0)]

def main(out):
    live=module('live'); schemas=json.loads(live.SCHEMAS.read_text(encoding='utf8'))
    policies=json.loads(live.POLICIES.read_text(encoding='utf8'))
    old=json.loads((ROOT/'artifacts/clarification_main/deferred_v4_1_heldout20_live_20261025/SELECTION.json').read_text(encoding='utf8'))
    oldids={(r['question_id'],r['level']) for r in old['cases']}
    rows=[]
    for q,l in IDS:
        if (q,l) in oldids: raise ValueError('already used')
        source=live.load_source(live.DATA,q); db=source['db_id']
        prior=next((r for r in old['cases'] if r['db_id']==db),None)
        role=next(p for p in policies if p['db_id']==db and (prior is None or (p['role']==prior['role'] and p['metadata']['question_id']==prior['policy_source_question_id'])))
        context=live.schema_context(schemas[db]); allowed=live.engine_module('policy').allowed_columns(context,role['policy'])
        rows.append(dict(question_id=q,level=l,db_id=db,source_sha256=source['source_sha256'],
            public_request=source['levels'][l]['rewritten_request'],role=role['role'],
            policy_source_question_id=role['metadata']['question_id'],allowed_columns=allowed,
            policy_sha256=hashlib.sha256(json.dumps(role['policy'],sort_keys=True).encode()).hexdigest()))
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(dict(seed=20261026,cases=rows,selection_before_live=True),ensure_ascii=False,indent=2),encoding='utf8')
    print('PREPARED',[(r['question_id'],r['level'],r['db_id']) for r in rows])
if __name__=='__main__':
    import sys;main(Path(sys.argv[1]))

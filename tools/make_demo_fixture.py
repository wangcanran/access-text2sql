"""Create a synthetic three-level fixture; not a paper evaluation sample."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    out = ROOT / 'examples/synthetic_sales'
    out.mkdir(parents=True, exist_ok=True)
    source = dict(question_id=0, db_id='synthetic_sales', dataset='synthetic',
        original_question_id=0,
        question='List product names and customer emails for returned orders in October 2026.',
        levels={
            '0': {'rewritten_request': 'Show product and customer details for orders matching a status and time period.'},
            '1': {'rewritten_request': 'Show product and customer details for returned orders in October 2026.'},
            '2': {'rewritten_request': 'List product names and customer emails for returned orders in October 2026.'}},
        flat_units=[
            {'item_id': 'o1', 'role': 'output', 'span': 'product names'},
            {'item_id': 'o2', 'role': 'output', 'span': 'customer emails'},
            {'item_id': 'm1', 'role': 'modifier', 'span': 'for returned orders in October 2026'}],
        reference_sql="SELECT product_name, customer_email FROM orders WHERE status = 'returned' AND order_date >= '2026-10-01' AND order_date < '2026-11-01';")
    source['source_sha256'] = hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()
    columns = [('order_id','INTEGER'),('product_name','TEXT'),('customer_email','TEXT'),('status','TEXT'),('order_date','TEXT')]
    schema = {'tables': {'orders': {'columns': [dict(cid=i,name=n,type=t,pk=int(i==0),notnull=0,dflt_value=None) for i,(n,t) in enumerate(columns)], 'foreign_keys': []}}, 'descriptions': {}}
    policy = {'orders': ['order_id','product_name','status','order_date']}
    roles = [dict(db_id='synthetic_sales', role='SalesAnalyst',policy=policy,metadata={'question_id':0})]
    def write(name, data):
        p = out/name
        p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        return hashlib.sha256(p.read_bytes()).hexdigest()
    hashes = {n:write(n,d) for n,d in [('sources.json',{'0':source}),('roles.json',roles),('schemas.json',{'synthetic_sales':schema})]}
    cases = [dict(question_id=0,level=level,db_id='synthetic_sales',dataset='synthetic',original_question_id=0,role='SalesAnalyst',policy_source_question_id=0,source_sha256=source['source_sha256'],policy_sha256=hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest(),allowed_columns=sorted('orders.'+c for c in policy['orders']),original_permission='denied') for level in range(3)]
    write('selection.json',dict(design='synthetic_demo_only',fixture_payload_sha256=hashes,cases=cases))
    write('MANIFEST.json',dict(purpose='Synthetic format and runtime example; excluded from all paper metrics.',requests=3,questions=1,roles=1))
    print('Created examples/synthetic_sales (3 synthetic requests)')

if __name__=='__main__':
    main()

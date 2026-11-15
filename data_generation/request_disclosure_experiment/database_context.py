"""Build fresh BIRD wording using v7's deterministic CSV convention, not v7 outputs."""
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from .prompts import DATABASE_CONTEXT_TEMPLATE

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_ROOT = ROOT/'rbac-exp/Bird/dev_20251106/dev_databases'


def build_database_context(db_id, raw_schema, description_root=DEFAULT_DB_ROOT):
    tables=[]; foreign_keys=[]; language={}; files=[]; missing=[]
    for name, table in raw_schema['tables'].items():
        tables.append({'name':name,'columns':[{'name':c['name'],'type':c['type']} for c in table['columns']]})
        for fk in table['foreign_keys']:
            foreign_keys.append({'from_table':name,'from_column':fk['from'],'to_table':fk['table'],
                                 'to_column':fk['to'],'constraint_id':fk['id'],'sequence':fk['seq'],
                                 'source':'sqlite_pragma_snapshot'})
        path=Path(description_root)/db_id/'database_description'/f'{name}.csv'
        rows={}
        if path.exists():
            data=path.read_bytes()
            for encoding in ('utf-8-sig','cp1252','latin-1'):
                try: text=data.decode(encoding); break
                except UnicodeDecodeError: continue
            for row in csv.DictReader(io.StringIO(text,newline='')):
                key=(row.get('original_column_name') or '').strip().casefold()
                if key: rows[key]=row
            files.append({'path':str(path.resolve()),'sha256':hashlib.sha256(data).hexdigest(),'encoding':encoding})
        else: missing.append(str(path))
        for column in table['columns']:
            row=rows.get(column['name'].casefold(),{})
            clean=lambda v: re.sub(r'\s+',' ',v or '').strip()
            desc=clean(row.get('column_description')); values=clean(row.get('value_description'))
            phrase=f'{desc} (Values: {values})' if desc and values else desc or (f'Values: {values}' if values else '')
            source='bird_database_description' if phrase else 'identifier_fallback'
            if not phrase: phrase=clean(row.get('column_name')) or column['name'].replace('_',' ')
            language[f"{name}.{column['name']}"]={'exact':phrase,'paraphrase':phrase,'description_source':source}
    return {'db_name':db_id,'db_schema':{'tables':tables,'foreign_keys':foreign_keys},
            'data_description':language}, {'mode':'fresh_csv_v7_deterministic_convention',
                'files':files,'missing_files':missing,'v7_outputs_read':False,
                'schema_source':'artifacts/bird_mini_dev_quality_audit_20261009/schemas.json'}


def with_database_context(prompt, context):
    """Retain the existing task prompt; database metadata is interpretive, not an answer."""
    if context is None: return prompt
    header=DATABASE_CONTEXT_TEMPLATE.format(db_name=json.dumps(context['db_name']),
        db_schema=json.dumps(context['db_schema'],ensure_ascii=False),
        data_description=json.dumps(context['data_description'],ensure_ascii=False))
    first,sep,rest=prompt.partition('\n')
    return first+header+sep+rest

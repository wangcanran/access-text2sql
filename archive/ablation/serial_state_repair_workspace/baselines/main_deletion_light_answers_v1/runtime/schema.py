def schema_context(raw):
    tables=[]; fks=[]; meanings={}
    table_names = {name.casefold(): name for name in raw['tables']}
    for name,t in raw['tables'].items():
        tables.append({'name':name,'columns':[{'name':c['name'],'type':c['type']} for c in t['columns']]})
        for fk in t['foreign_keys']:
            target = table_names.get(fk['table'].casefold(), fk['table'])
            target_columns = raw['tables'].get(target, {}).get('columns', [])
            target_column = fk['to']
            if target_column is None:
                # SQLite REFERENCES parent without a column list names the
                # parent's ordered primary key, not an unknown target column.
                primary = sorted((c for c in target_columns if c.get('pk', 0)), key=lambda c: c['pk'])
                parts = [p for p in t['foreign_keys'] if p['id'] == fk['id']]
                if len(primary) != len(parts) or not 0 <= fk['seq'] < len(primary):
                    raise ValueError(f'Cannot resolve implicit foreign key {name} -> {target}')
                target_column = primary[fk['seq']]['name']
            else:
                target_column = next((c['name'] for c in target_columns
                                      if c['name'].casefold() == target_column.casefold()), target_column)
            fks.append({'from_table':name,'from_column':fk['from'],'to_table':target,
                        'to_column':target_column,'constraint_id':fk['id'],'sequence':fk['seq'],'source':'sqlite_pragma'})
        for d in raw['descriptions'].get(name,{}).get('rows',[]):
            meanings[name+'.'+d['original_column_name']]=' | '.join(str(d[k]) for k in
                ('column_name','column_description','value_description','') if d.get(k))
    return {'tables':tables,'foreign_keys':fks,'column_language':meanings}


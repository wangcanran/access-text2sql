"""Expand and validate explicit table/column policies against the schema."""


def allowed_columns(context, policy):
    by_table = {table['name']: table for table in context.get('tables', [])}
    allowed = set()
    for table_name, names in policy.items():
        if table_name not in by_table:
            raise ValueError(f'policy table absent from schema: {table_name}')
        if names == ['*']:
            names = [column['name'] for column in by_table[table_name].get('columns', [])]
        known = {column['name'] for column in by_table[table_name].get('columns', [])}
        if not isinstance(names, list) or not set(names) <= known:
            raise ValueError(f'policy contains invalid columns for {table_name}')
        allowed.update(f'{table_name}.{name}' for name in names)
    return sorted(allowed)

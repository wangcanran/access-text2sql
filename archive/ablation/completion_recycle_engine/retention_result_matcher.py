"""Deterministic matching of explicit, identity-bound goal projections.

Mappings are inputs, not selected to maximize a method's score. Comparisons use
the complete projected result, retain multiplicity by default, and never confuse
an unavailable result with a genuine empty result. No model API is used.
"""
from collections import Counter
from datetime import date, datetime, time
from decimal import Decimal, localcontext
import json

VERSION='identity_bound_result_projection_v1'
SUBSET_VERSION='gold_projection_subset_coverage_v1'

def cell_key(value,numeric_decimals=8):
    if value is None:return ('null',)
    if isinstance(value,bool):return ('bool',value)
    if isinstance(value,(int,float,Decimal)):
        number=Decimal(str(value))
        if not number.is_finite():return ('number_special',str(number))
        with localcontext() as context:
            context.prec=max(50,len(number.as_tuple().digits)+abs(number.as_tuple().exponent)+numeric_decimals+5)
            number=number.quantize(Decimal(1).scaleb(-numeric_decimals))
        if number==0:number=abs(number)
        return ('number',str(number))
    if isinstance(value,str):return ('text',value)
    if isinstance(value,(bytes,bytearray,memoryview)):return ('bytes',bytes(value).hex())
    if isinstance(value,(datetime,date,time)):return (type(value).__name__,value.isoformat())
    if isinstance(value,(dict,list,tuple)):
        return ('json',json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,default=str))
    raise TypeError('Unsupported database value type: '+type(value).__name__)

def project(rows,indices,numeric_decimals=8):
    if not indices or len(indices)!=len(set(indices)):
        raise ValueError('Goal projection requires distinct, explicitly mapped columns')
    if any(type(i) is not int or i<0 for i in indices):raise ValueError('Invalid output index')
    return [tuple(cell_key(row[i],numeric_decimals) for i in indices) for row in rows]

def compare_goal(gold_rows,candidate_rows,gold_columns,candidate_columns,*,
                 order_sensitive=False,duplicate_policy='bag',numeric_decimals=8,
                 mapping_reviewed=False,empty_mapping_supported=False):
    """Compare complete goal tuples, including their predeclared entity anchors.

    mapping_reviewed denotes a separately documented mapping decision; it does
    not imply human review. If mappings are still unknown, no score is emitted.
    Empty results need a supported mapping because data alone cannot identify
    which of several empty columns corresponds to a requested output.
    """
    if duplicate_policy not in ('bag','set'):raise ValueError('Unknown duplicate policy')
    if type(numeric_decimals) is not int or not 0<=numeric_decimals<=15:raise ValueError('Invalid numeric precision')
    if len(gold_columns)!=len(candidate_columns):raise ValueError('Projection width mismatch')
    base=dict(comparator_version=VERSION,numeric_decimals=numeric_decimals,
              order_sensitive=order_sensitive,duplicate_policy=duplicate_policy,
              gold_columns=gold_columns,candidate_columns=candidate_columns)
    if gold_rows is None or candidate_rows is None:
        return dict(base,satisfied=None,status='execution_result_unavailable')
    if not mapping_reviewed:return dict(base,satisfied=None,status='output_mapping_pending')
    try:
        gold=project(gold_rows,gold_columns,numeric_decimals)
        candidate=project(candidate_rows,candidate_columns,numeric_decimals)
    except IndexError:
        return dict(base,satisfied=False,status='required_output_missing')
    if not gold and not candidate and not empty_mapping_supported:
        return dict(base,satisfied=None,status='empty_result_mapping_pending')
    if order_sensitive:
        if duplicate_policy=='set':
            gold=list(dict.fromkeys(gold));candidate=list(dict.fromkeys(candidate))
        equal=gold==candidate
    else:
        equal=(Counter(gold)==Counter(candidate)) if duplicate_policy=='bag' else set(gold)==set(candidate)
    return dict(base,satisfied=equal,status='goal_result_matches' if equal else 'goal_result_differs',
                gold_row_count=len(gold),candidate_row_count=len(candidate))

def compare_goal_subset(gold_rows,candidate_rows,gold_columns,candidate_columns,*,
                        duplicate_policy='bag',numeric_decimals=8,
                        mapping_reviewed=False,empty_mapping_supported=False,
                        row_selector=None,association_columns=None,
                        association_candidate_columns=None,row_window=None,
                        selector_candidate_column=None,window_candidate_column=None):
    """Score how much of a predeclared Gold goal projection the candidate returns.

    Retention is recall over distinct Gold tuples (set) or Gold row multiplicity
    (bag). Extra candidate tuples do not increase retention and are reported as
    precision/error evidence. Mapping and authorization remain separate gates.
    """
    if duplicate_policy not in ('bag','set'):raise ValueError('Unknown duplicate policy')
    base=dict(comparator_version=SUBSET_VERSION,numeric_decimals=numeric_decimals,
              duplicate_policy=duplicate_policy,gold_columns=gold_columns,
              candidate_columns=candidate_columns)
    if gold_rows is None or candidate_rows is None:
        return dict(base,retention=None,precision=None,matched_rows=None,
                    status='execution_result_unavailable')
    if not mapping_reviewed:
        return dict(base,retention=None,precision=None,matched_rows=None,
                    status='output_mapping_pending')
    try:
        if association_columns is not None:
            if association_candidate_columns is None or len(association_columns)!=len(association_candidate_columns):
                raise ValueError('Association binding is required')
            gold_columns=list(association_columns);candidate_columns=list(association_candidate_columns)
            base.update(gold_columns=gold_columns,candidate_columns=candidate_columns,
                        comparison_scope='joint_association_tuple')
        if len(gold_columns)!=len(candidate_columns):raise ValueError('Projection width mismatch')
        if row_selector is not None:
            index=row_selector['column'];operator=row_selector['operator'];target=cell_key(row_selector['value'],numeric_decimals)
            label_pos=gold_columns.index(index) if index in gold_columns else None
            def selected(rows,column):
                return [row for row in rows if (cell_key(row[column],numeric_decimals)==target)==(operator=='eq')]
            gold_rows=selected(gold_rows,index)
            candidate_rows=selected(candidate_rows,selector_candidate_column if selector_candidate_column is not None
                                    else candidate_columns[label_pos])
            base['row_selector']=row_selector
        if row_window is not None:
            index=row_window['partition_column'];count=row_window['count'];side=row_window['side']
            label_pos=gold_columns.index(index) if index in gold_columns else None
            def windowed(rows,column):
                groups={}
                for i,row in enumerate(rows):groups.setdefault(cell_key(row[column],numeric_decimals),[]).append(i)
                chosen={i for ids in groups.values() for i in (ids[:count] if side=='head' else ids[-count:])}
                return [row for i,row in enumerate(rows) if i in chosen]
            gold_rows=windowed(gold_rows,index)
            candidate_rows=windowed(candidate_rows,window_candidate_column if window_candidate_column is not None
                                    else candidate_columns[label_pos])
            base['row_window']=row_window
        gold=project(gold_rows,gold_columns,numeric_decimals)
        candidate=project(candidate_rows,candidate_columns,numeric_decimals)
    except IndexError:
        return dict(base,retention=0.0,precision=0.0,matched_rows=0,
                    status='required_output_missing')
    if not gold and not empty_mapping_supported:
        return dict(base,retention=None,precision=None,matched_rows=None,
                    status='empty_gold_reference_pending')
    if duplicate_policy=='set':
        g=set(gold);c=set(candidate);matched=len(g & c)
        gold_n=len(g);candidate_n=len(c);extra=len(c-g)
    else:
        g=Counter(gold);c=Counter(candidate)
        matched=sum(min(n,c.get(row,0)) for row,n in g.items())
        gold_n=len(gold);candidate_n=len(candidate)
        extra=sum(max(0,n-g.get(row,0)) for row,n in c.items())
    retention=matched/gold_n if gold_n else 1.0
    precision=matched/candidate_n if candidate_n else (1.0 if gold_n==0 else 0.0)
    return dict(base,retention=retention,precision=precision,matched_rows=matched,
                gold_rows=gold_n,candidate_rows=candidate_n,extra_candidate_rows=extra,
                status='scored')

def safe_goal_delivery(comparison,gate,gate_reason):
    if gate not in (0,1,None):raise ValueError('Invalid gate')
    if gate==0:
        if gate_reason not in ('no_sql','execution_failed','unauthorized'):raise ValueError('Uncertified gate failure')
        return dict(satisfied=False,status='delivery_gate_failed',gate_reason=gate_reason)
    if gate is None:return dict(satisfied=None,status='delivery_gate_pending')
    if gate_reason!='authorized_and_executed':raise ValueError('Gate success evidence missing')
    return dict(satisfied=comparison['satisfied'],status=comparison['status'],gate_reason=gate_reason)

def find_goal_projection(gold_rows,candidate_rows,gold_columns,candidate_width,*,
                         order_sensitive=False,duplicate_policy='bag',numeric_decimals=8,
                         empty_candidate_columns=None,max_checks=20000,row_selector=None,_fixed_mapping=None,
                         reference_transform=None,association_columns=None,
                         empty_association_candidate_columns=None,row_window=None):
    """Find an injective result-column mapping by complete joint-result equality.

    This is an execution metric on the current database. SQL-expression identity
    is not required: alternative implementations can earn the same result score.
    Entity anchors must be included in gold_columns before the search begins.
    """
    if row_window is not None:
        if row_selector is not None or reference_transform is not None or association_columns is not None:
            raise ValueError('Grouped row windows cannot combine with other transforms')
        return find_window_goal_projection(gold_rows,candidate_rows,gold_columns,candidate_width,
            row_window=row_window,order_sensitive=order_sensitive,duplicate_policy=duplicate_policy,
            numeric_decimals=numeric_decimals,empty_candidate_columns=empty_candidate_columns,max_checks=max_checks)
    if association_columns is not None:
        if row_selector is not None or reference_transform is not None or _fixed_mapping is not None:
            raise ValueError('Association certificates cannot combine with result transforms')
        if not set(gold_columns).issubset(association_columns):
            raise ValueError('Association certificate must include the goal outputs')
        marginal=find_goal_projection(gold_rows,candidate_rows,gold_columns,candidate_width,
            order_sensitive=order_sensitive,duplicate_policy=duplicate_policy,numeric_decimals=numeric_decimals,
            empty_candidate_columns=empty_candidate_columns,max_checks=max_checks)
        # A wrong complete marginal disproves delivery. A matching marginal alone
        # does not establish that two attributes belong to the same reference row.
        if marginal['satisfied'] is False:
            return {**marginal,'association_columns':association_columns,
                    'association_policy':'marginal_disproof_joint_confirmation'}
        joint=find_goal_projection(gold_rows,candidate_rows,association_columns,candidate_width,
            order_sensitive=order_sensitive,duplicate_policy=duplicate_policy,numeric_decimals=numeric_decimals,
            empty_candidate_columns=empty_association_candidate_columns,max_checks=max_checks)
        if joint['satisfied'] is True:
            mapping=dict(zip(association_columns,joint['candidate_columns']))
            return {**marginal,'satisfied':True,'status':'joint_reference_association_certified',
                    'candidate_columns':[mapping[c] for c in gold_columns],
                    'association_columns':association_columns,
                    'association_candidate_columns':joint['candidate_columns'],
                    'association_policy':'marginal_disproof_joint_confirmation'}
        return {**marginal,'satisfied':None,'status':'reference_association_unresolved',
                'association_columns':association_columns,
                'association_policy':'marginal_disproof_joint_confirmation',
                'marginal_status':marginal['status'],'joint_status':joint['status']}
    if reference_transform is not None:
        if reference_transform!={'operation':'row_count'} or row_selector is not None:
            raise ValueError('Unsupported reference transform')
        transformed=None if gold_rows is None else [(len(gold_rows),)]
        result=find_goal_projection(transformed,candidate_rows,[0],candidate_width,
            order_sensitive=False,duplicate_policy='bag',numeric_decimals=numeric_decimals,
            max_checks=max_checks)
        return {**result,'reference_transform':reference_transform,'original_gold_columns':gold_columns}
    if row_selector is not None:
        return find_selected_goal_projection(gold_rows,candidate_rows,gold_columns,candidate_width,
            row_selector=row_selector,order_sensitive=order_sensitive,duplicate_policy=duplicate_policy,
            numeric_decimals=numeric_decimals,empty_candidate_columns=empty_candidate_columns,max_checks=max_checks)
    base=dict(comparator_version=VERSION,mapping_policy='joint_result_injective_projection',
              gold_columns=gold_columns,candidate_columns=None)
    if duplicate_policy not in ('bag','set'):raise ValueError('Unknown duplicate policy')
    if type(numeric_decimals) is not int or not 0<=numeric_decimals<=15:raise ValueError('Invalid numeric precision')
    if type(candidate_width) is not int or candidate_width<0:raise ValueError('Invalid candidate width')
    if type(max_checks) is not int or max_checks<1:raise ValueError('Invalid search budget')
    if gold_rows is None or candidate_rows is None:
        return dict(base,satisfied=None,status='execution_result_unavailable')
    if len(gold_columns)>candidate_width:
        return dict(base,satisfied=False,status='required_output_width_missing')
    gold=project(gold_rows,gold_columns,numeric_decimals)
    if not gold and not candidate_rows:
        if empty_candidate_columns is None:
            return dict(base,satisfied=None,status='empty_result_mapping_pending')
        return compare_goal(gold_rows,candidate_rows,gold_columns,empty_candidate_columns,
            order_sensitive=order_sensitive,duplicate_policy=duplicate_policy,
            numeric_decimals=numeric_decimals,mapping_reviewed=True,empty_mapping_supported=True)
    if duplicate_policy=='bag' and len(gold_rows)!=len(candidate_rows):
        return dict(base,satisfied=False,status='goal_result_row_count_differs',
                    gold_row_count=len(gold_rows),candidate_row_count=len(candidate_rows))
    candidate=[tuple(cell_key(v,numeric_decimals) for v in row) for row in candidate_rows]
    if any(len(row)!=candidate_width for row in candidate):raise ValueError('Ragged candidate result')
    checks=0;exhausted=False
    def relation(rows):
        if order_sensitive:
            return rows if duplicate_policy=='bag' else list(dict.fromkeys(rows))
        return Counter(rows) if duplicate_policy=='bag' else set(rows)
    expected=[relation([row[:depth] for row in gold]) for depth in range(1,len(gold_columns)+1)]
    def search(mapping):
        nonlocal checks,exhausted
        if len(mapping)==len(gold_columns):return mapping
        for j in range(candidate_width):
            if _fixed_mapping is not None and len(mapping) in _fixed_mapping and j!=_fixed_mapping[len(mapping)]:continue
            if j in mapping:continue
            checks+=1
            if checks>max_checks:exhausted=True;return None
            selected=mapping+[j]
            if relation([tuple(row[k] for k in selected) for row in candidate])!=expected[len(selected)-1]:continue
            found=search(selected)
            if found is not None:return found
            if exhausted:return None
        return None
    mapping=search([])
    if mapping is not None:
        return {**base,'satisfied':True,'status':'goal_result_matches','candidate_columns':mapping,
                'mapping_checks':checks,'gold_row_count':len(gold_rows),'candidate_row_count':len(candidate_rows)}
    return dict(base,satisfied=None if exhausted else False,
                status='projection_search_pending' if exhausted else 'goal_result_differs',mapping_checks=checks)


def find_selected_goal_projection(gold_rows,candidate_rows,gold_columns,candidate_width,*,
        row_selector,order_sensitive=False,duplicate_policy='bag',numeric_decimals=8,
        empty_candidate_columns=None,max_checks=20000):
    """Compare a predeclared labeled part of a mixed result, retaining its label.

    The selector column must be in the goal tuple. Candidate label positions may
    differ, but the same literal and predicate apply to Gold and candidate rows.
    A successful mapping must bind that exact label position, preventing a value
    in an unrelated column from selecting a favorable subset.
    """
    if set(row_selector)!={'column','operator','value'}:
        raise ValueError('Invalid row selector fields')
    index=row_selector['column'];operator=row_selector['operator']
    if type(index) is not int or index not in gold_columns or operator not in ('eq','ne'):
        raise ValueError('Row selector requires an included label column and eq/ne operator')
    base=dict(comparator_version=VERSION,gold_columns=gold_columns,row_selector=row_selector,
              mapping_policy='predeclared_labeled_result_partition',candidate_columns=None)
    if gold_rows is None or candidate_rows is None:
        return dict(base,satisfied=None,status='execution_result_unavailable')
    target=cell_key(row_selector['value'],numeric_decimals)
    def select(rows,column):
        return [row for row in rows if (cell_key(row[column],numeric_decimals)==target)==(operator=='eq')]
    gold=select(gold_rows,index)
    label_position=gold_columns.index(index)
    pending=False;checks=0
    for candidate_label in range(candidate_width):
        selected=select(candidate_rows,candidate_label)
        # Bind the label explicitly, then search only the remaining attributes.
        # Permuting the candidate columns puts the selected label first.
        ordered=[candidate_label]+[j for j in range(candidate_width) if j!=candidate_label]
        reordered=[tuple(row[j] for j in ordered) for row in selected]
        goal_order=[index]+[j for j in gold_columns if j!=index]
        supported=None
        if empty_candidate_columns is not None and empty_candidate_columns[label_position]==candidate_label:
            supported=[ordered.index(empty_candidate_columns[gold_columns.index(j)]) for j in goal_order]
        result=find_goal_projection(gold,reordered,goal_order,candidate_width,
            order_sensitive=order_sensitive,duplicate_policy=duplicate_policy,numeric_decimals=numeric_decimals,
            empty_candidate_columns=supported,max_checks=max(1,max_checks-checks),_fixed_mapping={0:0})
        checks+=result.get('mapping_checks',0)
        if result['satisfied'] is True:
            found=result['candidate_columns']
            if found[0]!=0:
                # The selector is an explicit part of the comparison contract.
                continue
            mapping={g:ordered[c] for g,c in zip(goal_order,found)}
            return dict(base,satisfied=True,status='goal_result_matches',
                candidate_columns=[mapping[g] for g in gold_columns],candidate_selector_column=candidate_label,
                gold_row_count=len(gold),candidate_row_count=len(selected),mapping_checks=checks)
        pending |= result['satisfied'] is None
        if checks>=max_checks:
            return dict(base,satisfied=None,status='projection_search_pending',mapping_checks=checks)
    return dict(base,satisfied=None if pending else False,
                status='selected_result_mapping_pending' if pending else 'goal_result_differs',mapping_checks=checks)


def find_window_goal_projection(gold_rows,candidate_rows,gold_columns,candidate_width,*,
        row_window,order_sensitive=False,duplicate_policy='bag',numeric_decimals=8,
        empty_candidate_columns=None,max_checks=20000):
    """Compare the first/last k displayed rows per original-Gold group.

    The group label stays in every goal tuple. Windows preserve displayed order;
    no ranking on unavailable candidate scores and no favorable row subset search
    is performed. Reference provenance must establish what these windows mean.
    """
    if set(row_window)!={'partition_column','side','count'}:
        raise ValueError('Invalid grouped window fields')
    index=row_window['partition_column'];side=row_window['side'];count=row_window['count']
    if type(index) is not int or index not in gold_columns or side not in ('head','tail') or type(count) is not int or count<1:
        raise ValueError('Grouped window requires an included partition label and positive count')
    base=dict(comparator_version=VERSION,gold_columns=gold_columns,row_window=row_window,
              mapping_policy='predeclared_grouped_display_window',candidate_columns=None)
    if gold_rows is None or candidate_rows is None:
        return dict(base,satisfied=None,status='execution_result_unavailable')
    def select(rows,column):
        groups={}
        for i,row in enumerate(rows):groups.setdefault(cell_key(row[column],numeric_decimals),[]).append(i)
        chosen={i for positions in groups.values() for i in (positions[:count] if side=='head' else positions[-count:])}
        return [row for i,row in enumerate(rows) if i in chosen]
    gold=select(gold_rows,index);goal_order=[index]+[j for j in gold_columns if j!=index]
    pending=False;checks=0
    for label in range(candidate_width):
        selected=select(candidate_rows,label)
        ordered=[label]+[j for j in range(candidate_width) if j!=label]
        reordered=[tuple(row[j] for j in ordered) for row in selected]
        supported=None
        if empty_candidate_columns is not None and empty_candidate_columns[gold_columns.index(index)]==label:
            supported=[ordered.index(empty_candidate_columns[gold_columns.index(j)]) for j in goal_order]
        result=find_goal_projection(gold,reordered,goal_order,candidate_width,
            order_sensitive=order_sensitive,duplicate_policy=duplicate_policy,numeric_decimals=numeric_decimals,
            empty_candidate_columns=supported,max_checks=max(1,max_checks-checks),_fixed_mapping={0:0})
        checks+=result.get('mapping_checks',0)
        if result['satisfied'] is True:
            mapping={g:ordered[c] for g,c in zip(goal_order,result['candidate_columns'])}
            return dict(base,satisfied=True,status='goal_result_matches',
                candidate_columns=[mapping[g] for g in gold_columns],candidate_partition_column=label,
                gold_row_count=len(gold),candidate_row_count=len(selected),mapping_checks=checks)
        pending |= result['satisfied'] is None
        if checks>=max_checks:return dict(base,satisfied=None,status='projection_search_pending',mapping_checks=checks)
    return dict(base,satisfied=None if pending else False,
                status='window_result_mapping_pending' if pending else 'goal_result_differs',mapping_checks=checks)

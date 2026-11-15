"""Deterministic maximum partial injective result projection on a fixed Gold set."""
from collections import Counter
import time
from retention_result_matcher import project


def match_results(gold,candidate,legal,candidate_width,*,max_checks=20000,seconds=5.):
    if not legal:raise ValueError('Positive fixed legal reference required')
    cap=min(len(legal),candidate_width)/len(legal)
    if not gold:
        if candidate:return dict(status='scored',score_lower=0.,score_upper=0.,reason='nonempty_candidate_empty_gold')
        return dict(status='empty_reference_unresolved',score_lower=0.,score_upper=cap,
                    reason='empty_results_do_not_identify_output_correspondence')
    if not candidate or not candidate_width:
        return dict(status='scored',score_lower=0.,score_upper=0.,reason='empty_candidate_nonempty_gold')
    # Marginal containment is necessary for joint bag containment. This prunes
    # impossible edges without choosing mappings by marginal score alone.
    gm={i:Counter(project(gold,[i])) for i in legal}
    cm={j:Counter(project(candidate,[j])) for j in range(candidate_width)}
    options={i:[j for j,c in cm.items() if all(n<=gm[i].get(v,0) for v,n in c.items())] for i in legal}
    gold_cache={}
    best=0.;bestmap=[];checks=0;deadline=time.monotonic()+seconds;exhausted=True
    # For a valid bag subset, recall is exactly candidate row count / Gold row
    # count regardless of selected columns. Maximize jointly valid output count.
    row_fraction=len(candidate)/len(gold)
    order=sorted(legal,key=lambda i:(len(options[i]),i))
    def visit(position,mapping,used):
        nonlocal best,bestmap,checks,exhausted
        if not exhausted:return
        if len(mapping)+len(order)-position<=len(bestmap):return
        if time.monotonic()>deadline or checks>=max_checks:
            exhausted=False;return
        if mapping:
            cols=tuple(i for i,j in mapping);js=[j for i,j in mapping]
            if cols not in gold_cache:gold_cache[cols]=Counter(project(gold,list(cols)))
            cp=Counter(project(candidate,js));checks+=1
            if any(n>gold_cache[cols].get(v,0) for v,n in cp.items()):return
            value=len(mapping)/len(legal)*row_fraction
            if value>best:
                best=value;bestmap=list(mapping)
            if len(bestmap)==min(len(legal),candidate_width):return
        if position==len(order):return
        i=order[position]
        for j in options[i]:
            if j not in used:visit(position+1,mapping+[(i,j)],used|{j})
        visit(position+1,mapping,used)
    visit(0,[],set())
    # Exhaustiveness is needed for an exact maximum. A feasible mapping is
    # already a certified lower bound even if enumeration hits its resource cap.
    upper=best if exhausted else min(len(legal),candidate_width)/len(legal)*min(row_fraction,1.)
    return dict(status='scored' if exhausted else 'mapping_search_pending',score_lower=best,
                score_upper=upper,gold_columns=[i for i,j in bestmap],
                candidate_columns=[j for i,j in bestmap],joint_checks=checks,
                mapping_selected_by_results=True,gold_rows=len(gold),candidate_rows=len(candidate),
                score_definition='max_injective_partial_joint_bag_subset_coverage_v1')

"""Plot actual added-request scores, retaining the original A/P observations."""
from pathlib import Path
from collections import Counter
import argparse,csv,json,math
def main():
 p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();j=a.job
 assert (j/'COMPLETE.json').exists(),'All paired scores required before final plot'
 rows=[json.loads(s) for s in (j/'scores.jsonl').read_text().splitlines() if s.strip()]
 refs=[json.loads(s) for s in (j/'references.jsonl').read_text().splitlines() if s.strip()];plan=json.loads((j/'RUN_PLAN.json').read_text())
 old={r['method']:r for r in json.loads((j/'paper_metric_source.json').read_text())['main'] if r['model']=='deepseek_flash'}
 methods=plan['methods'];by={m:{r['request_id']:r for r in rows if r['method']==m} for m in methods};rmap={r['request_id']:r for r in refs}
 assert all(set(by[m])==set(rmap) for m in methods)
 assert all(s['score_lower']==s['score_upper'] and s['status']=='scored' for s in rows)
 names=dict(baseline='Direct + RBAC',serial='Serial',joint='Policy',main='ACCESS');colors=dict(baseline='#84909C',serial='#DA8A18',joint='#217CA3',main='#BD4038')
 ordered=[':'.join(str(c[k]) for k in ('question_id','level','role','policy_sha256')) for c in plan['ordered_cases']]
 assert len(ordered)==len(set(ordered))==len(refs)
 curves=[];subgroups=[]
 for scenario in ['all','partial_unchanged','partial_additive']:
  ids=[r for r in ordered if scenario=='all' or rmap[r]['scenario']==scenario]
  groups=[]
  for rid in ids:
   key=rid.split(':')[0]
   if not groups or groups[-1][0]!=key:groups.append((key,[]))
   groups[-1][1].append(rid)
  sizes=[0];n=0
  for _,g in groups:n+=len(g);sizes.append(n)
  target_sizes={0,len(ids)}
  for rho in plan['targets']:
   need=max(0,math.ceil(rho*1402/(1-rho))-80)
   choices=[v for v in sizes if v>=need]
   if choices:target_sizes.add(min(choices))
  for n in sorted(target_sizes):
   chosen=ids[:n];P=80+n;E=1482+n
   for m in methods:
    ss=[by[m][r] for r in chosen];o=old[m];rec=dict(scenario=scenario,method=names[m],added_P=n,total_P=P,total_A=1402,total_E=E,P_share_pct=100*P/E,added_unique_questions=len({tuple(rmap[r]['cluster_key']) for r in chosen}))
    for metric in ['SafeEX','Full']:
     value=sum(r['score_lower'] if metric=='SafeEX' else r['score_lower']==1 for r in ss)
     for k,label in enumerate(['lower','upper']):
      total=o['groups']['A'][metric][k]/100*1402+o['groups']['P'][metric][k]/100*80+value
      rec[metric+'_'+label]=100*total/E
     rec['new_P_'+metric]=100*value/n if n else None
    curves.append(rec)
  for m in methods:
   ss=[by[m][r] for r in ids]
   if ss:subgroups.append(dict(scenario=scenario,method=names[m],N=len(ss),SafeEX=100*sum(r['score_lower'] for r in ss)/len(ss),Full=100*sum(r['score_lower']==1 for r in ss)/len(ss),authorization_counts=dict(Counter(r['sql_access'] for r in ss))))
 with (j/'empirical_curve_points.csv').open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=list(curves[0]));w.writeheader();w.writerows(curves)
 (j/'new_P_metrics.json').write_text(json.dumps(subgroups,indent=2))
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'pdf.fonttype':42,'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False})
 fig,axs=plt.subplots(1,2,figsize=(10.5,4.2),layout='constrained')
 for ax,metric in zip(axs,['SafeEX','Full']):
  for m in methods:
   rr=[r for r in curves if r['scenario']=='all' and r['method']==names[m]];xs=[r['P_share_pct'] for r in rr];lo=[r[metric+'_lower'] for r in rr];hi=[r[metric+'_upper'] for r in rr]
   ax.plot(xs,lo,'o-',color=colors[m],label=names[m],lw=2.2 if m=='main' else 1.6,markersize=4);ax.fill_between(xs,lo,hi,color=colors[m],alpha=.13)
  ax.set_xlabel('P share in E = A ∪ P (%)');ax.set_ylabel(metric+' (%)');ax.grid(axis='y',alpha=.2);ax.axvline(100*80/1482,color='#666666',ls=':',lw=1)
 axs[0].legend(frameon=False,fontsize=9);fig.suptitle('Actual new-request evaluation · DeepSeek Flash')
 for ext in ['png','pdf','svg']:fig.savefig(j/('empirical_p_share.'+ext),dpi=220,bbox_inches='tight')
 (j/'EMPIRICAL_REPORT.md').write_text('# P占比实测实验\n\n新增请求在四个方法上重新执行澄清与SQL生成，按原顺序敏感口径评分。原有A/P分数保留，所有新增P使用实测得分。各档按预先固定的顺序加入问题/权限组，保留同组层级；横轴为实际P占比。\n\n图中阴影仅来自原有评分证据上下界，不是重复抽样置信区间。两类新增来源的单独曲线点保存在CSV中。\n\n新增请求数：'+str(len(refs))+'；四方法评测数：'+str(len(rows))+'。\n',encoding='utf8')
 print(json.dumps(dict(final_curves=True,new_requests=len(refs),method_scores=len(rows),outputs=['empirical_p_share.png','empirical_curve_points.csv','new_P_metrics.json'])))
if __name__=='__main__':main()

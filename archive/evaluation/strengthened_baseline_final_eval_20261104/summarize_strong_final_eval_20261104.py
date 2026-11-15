"""Validate aligned cohorts and publish the four-method DeepSeek comparison."""
import csv,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/strengthened_baselines_20261103/final_evaluation_20261104'
OLD=ROOT/'artifacts/extra_qwen_paired_2800_20261104/final_analysis'
def jl(p):return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]
def csvread(p):
 with p.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def main():
 old=[r for r in jl(OLD/'paired_classifications.jsonl') if r['model']=='deepseek_flash']
 cohorts={m:{r['id'] for r in old if r['method']==m} for m in ['baseline','main']}
 allret=[r for r in jl(OLD/'joint_candidate_scores.jsonl') if r['model']=='deepseek_flash']+jl(OUT/'joint_retention/candidate_scores_final_v1.jsonl')
 oldmetrics={r['method']:r for r in csvread(OLD/'metrics_overall.csv') if r['model']=='deepseek_flash'}
 rows=[];checks={}
 for method in ['baseline','serial','joint','main']:
  if method in oldmetrics:m=oldmetrics[method]
  else:
   data=jl(OUT/method/'classifications.jsonl');cohorts[method]={r['id'] for r in data}
   assert len(data)==2800 and len(cohorts[method])==2800
   m=next(r for r in csvread(OUT/method/'metrics.csv') if r['grouping']=='overall')
   state=json.loads((OUT/method/'PIPELINE_STATUS.json').read_text())
   assert state['state']=='complete' and state['scoring']['gold_errors']==0
   counts=dict(__import__('collections').Counter(r['category'] for r in data))
   assert all(counts.get(k,0)==int(m[k]) for k in ['C','W','CR','IR','VC','VW'])
   checks[method]=dict(N=len(data),pipeline=state['state'],gold_errors=0,category_counts=counts,sql_access=state['scoring']['sql_access'])
  rr=[r for r in allret if r['method']==method]
  assert len(rr)==80 and all(r['status']=='scored' for r in rr)
  rows.append(dict(method=method,model='deepseek_flash',N=2800,SafeEX=100*float(m['safe_ex']),AC_F1=100*float(m['f1']),VR=100*float(m['violation_rate']),ORR=100*float(m['over_refusal_rate']),retention_N=80,joint_retention=100*sum(r['joint_retention'] for r in rr)/80,any_retained=sum(r['joint_retention']>0 for r in rr)))
 assert len(cohorts['main'])==2800 and all(v==cohorts['main'] for v in cohorts.values())
 retids={r['request_id'] for r in allret if r['method']=='main'}
 assert len(retids)==80 and all({r['request_id'] for r in allret if r['method']==m}==retids for m in cohorts)
 with (OUT/'FOUR_METHOD_COMPARISON.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
 report=['# 强 Baseline 后端与评测完成记录','', '后端：DeepSeek Flash；完整请求比较：同一组 2,800 个 ID（allowed 1,402；denied 1,398）。需求保留：同一组 80 个正容量 denied 请求，28 个原始问题簇。','', '|方法|SafeEX ↑|AC-F1 ↑|VR ↓|ORR ↓|联合 Gold 保留率 ↑|正保留请求 / 80|','|---|---:|---:|---:|---:|---:|---:|']
 for r in rows:report.append('|'+r['method']+'|'+ '|'.join(f"{r[k]:.2f}%" for k in ['SafeEX','AC_F1','VR','ORR','joint_retention'])+f"|{r['any_retained']}|")
 report+=['','两个强 Baseline 的前端均为 2,800 条，后端分别处理串行 2,800 条和联合 2,381 条；联合前端的 419 条明确拒绝直接进入完整请求评测。生成技术错误和 Gold 执行错误均为零。','', '完整请求指标沿用 SCORING_PROTOCOL_ZH.md：串行按后端显式拒绝判定，联合按前端拒绝或显式剪枝判定。VR 是完整请求决策指标，与实际 SQL 依赖安全审计分开。SafeEX 分母为原始 allowed 1,402 条。','', '需求保留沿用固定合法 Gold 输出的联合投影、多重集和原比较器；80 个请求等权平均。15 个初始未决项已完成 SQL 源码列对应/依赖复核，最终 160/160 有分数。源码复核由 Codex 完成，不宣称为独立人工标注。映射对应不等于结果正确；结果仍由冻结 Gold 与候选原始 SQL 的执行结果决定。证据见 joint_retention/source_review_v1.jsonl。','', '初始评测文件原样保存，最终需求保留使用 joint_retention/candidate_scores_final_v1.jsonl 与 STATUS_FINAL_V1.json。serial 的 crypto_exchange 候选读取受限 walletSum 与 ACCTBAL_NODE，复核后记零；museum_artifact 的两条候选依赖均获授权。','', '对比现象：两个强 Baseline 的 SafeEX 高于主方法；主方法的完整请求 VR 更低，合法联合输出保留率更高。此表为描述性结果，新比较未沿用旧图显著性星号。','', '完整 SQL 依赖诊断仍有串行 205、联合 194 条解析未决；这不是生成任务失败。80 条需求保留子集所需的权限门控已全部解决。','', '服务器结果目录：/path/to/access/output/strengthened_baseline_final_eval_20261104','', '复现：服务器运行 scripts/run_strong_backend_server_20261104.py 的既有流程（不要重复生成已完成结果）；需求保留初评 scripts/score_strong_retention_server_20261104.py，再运行 scripts/finalize_strong_retention_20261104.py；本地运行 scripts/summarize_strong_final_eval_20261104.py 重建汇总。']
 (OUT/'EVALUATION_REPORT_ZH.md').write_text('\n'.join(report)+'\n',encoding='utf-8')
 (OUT/'COMPLETION_CHECKS.json').write_text(json.dumps(dict(cohort_ids_identical=True,cohort_N=2800,retention_ids_identical=True,retention_N=80,retention_scored=160,checks=checks,summary=rows),indent=2),encoding='utf-8')
 print(json.dumps(rows,indent=2))
if __name__=='__main__':main()

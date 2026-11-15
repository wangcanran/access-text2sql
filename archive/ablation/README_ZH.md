# 四组消融实验与复现

服务器目录：`/path/to/access/output/four_arm_terminal_20261106`。

原始运行与冻结源码：`/path/to/access/output/four_arm_executable_20261105`。

四组采用同一批2,800条请求。完整方法复用已核验的历史结果，另外三组实际运行生命周期干预。

|组别|保留的机制|干预|
|---|---|---|
|full|完整方法|复用历史主方法结果|
|no_pruning|澄清及真实权限信息|需要剪枝时拒绝整个请求，不交付部分需求|
|no_clarification|内部分析及权限剪枝|不调用模拟用户，保留原有内部处理|
|clarify_then_prune|澄清与剪枝模块|先完成澄清，再剪枝；进入剪枝阶段后不再追问|

## 补跑与结果选择

串行组已修正澄清阶段的状态衔接：权限探测继续保存在决策台账中，不再混入仅支持scope/field/operation/value的业务歧义字段。修复通过11项边界测试及真实失败样本的状态回放；6条完整流程验证完成4条，另2条为其他输出格式失败。代码快照为`serial_state_repair_workspace`，启用证据为`SERIAL_STATE_REPAIR_ACTIVE.json`。

修复后的串行组从同一批2,800条重新运行，不混用旧实现的成功样本。旧补跑结果归档于`superseded_clarify_then_prune_before_state_repair`，原始运行仍在`four_arm_executable_20261105`。最终串行组的重试成本按修复版全部尝试计算；旧实现的调用记录单独保留。

`complete_four_arm_terminal_cohort.py`对未决和错误请求补跑，每组最多20轮。每次仍调用冻结运行器，提示词、模型及单次轮数预算不变。选择该请求首次真正形成的deliver或refuse，不根据SQL正确性或Gold结果挑选重试。

原始记录与每轮trace均保留。每轮目录为`<arm>/retries/round_XX`；选定结果写入`<arm>/evaluation_rows.jsonl`及`SELECTED_TRACES.json`。如果仍有非终态请求，流水线停止在`needs_cause_repair`，等待诊断，不将其改写为拒绝。

只对deliver生成SQL。相同请求、相同选定trace和相同后端输入的历史SQL检查点可以复用，证据见`<arm>/REUSE_AUDIT.json`。SQL评分复用原始评分函数。

`audit_selected_four_arm_traces.py`检查选定trace的哈希、请求身份、权限输入、源码一致性和消融边界。`audit_four_arm_retry_effort.py`统计全部已记录尝试的调用次数及token，包括失败重试。

## 指标

旧版指标直接复用`analyze_paired_main_20261102.py`中的分类和汇总函数：VR、OR的分母均为2,800，SafeEX的分母为原始allowed中的1,402条；完整方法已逐项复现并与历史PPT数据核对一致。

需求保留使用`strict_ordered_evaluation`中的冻结严格参考：原始denied共1,398条，其中77条具有正容量。评分使用联合列结果，检查完整结果、重复次数以及Gold要求的行序。LOR为联合匹配恢复的合法列数占固定合法列数的比例，JFR为完整恢复全部合法输出的请求比例。

旧版指标与严格需求保留指标分别保留定义。全部重试成本计入成本统计。

配对统计以原始问题簇为单位。旧版VR、OR、SafeEX使用簇bootstrap置信区间和配对标签置换，每项指标的三个计划比较作Holm校正；需求保留使用严格评测目录中的配对统计结果。

## 本地收集与汇总

在仓库根目录运行：

```powershell
D:/Anaconda/python.exe -X utf8 scripts/monitor_four_arm_terminal_cohort.py --collect
D:/Anaconda/python.exe -X utf8 scripts/summarize_four_arm_terminal.py
```

收集器按服务器文件SHA-256核验下载内容。结果保存到本目录。`final_analysis/STATUS.json`指示汇总是否完成；实验或评分尚未齐备时只生成等待状态，不输出正式消融比较。

最终表格：`final_analysis/metrics.csv`。

旧版指标配对统计：`final_analysis/legacy_paired_statistics.csv`。

严格需求保留配对统计：`strict_ordered_evaluation/paired_statistics.csv`。

历史完整方法复现：`final_analysis/HISTORICAL_FULL_REPRODUCTION.json`。

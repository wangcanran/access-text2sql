# 消融实验未完成样本的恢复记录

每组固定同一批 2,800 条请求。仅原始校验器认可的 `deliver/refuse` 算完成，未决、错误不删分母、不改标签。每条选择按轮次排序后的第一个真实完成结果；后续尝试不能替换已完成结果。

## 本次恢复范围

服务器目录：`/path/to/access/output/four_arm_terminal_20261106`。

串行组使用 `serial_state_repair_workspace` 完整重跑 2,800 条。串行工作进程使用固定的修复后源码；调度恢复与错误反馈适配独立于实验方法实现，按下文记录。

去澄清、去剪枝组的剩余失败使用可追踪的错误反馈恢复：

- 首次调用的提示词、输入及所有原有校验器不变。
- 仅在既有 `_contract_retry` 中补充 `contract_error` 的具体诊断。
- 诊断依据只有已有响应、请求上下文及确认过的用户选择。不读取 Gold SQL、评测标签或参考结果。
- 不改模型响应，不替模型填字段，不跳过语义、权限、继承或执行门控。
- 多选反馈列出用户已经选择的全部依赖，明确多选不是由系统再择一。
- 每个工具版本独立保存，运行后不覆盖。每轮保留完整 trace、选择 ID、源文件 SHA256、日志和调用成本。

原有重试轮次和恢复轮次均应进入最终成本统计；该恢复不是原始单次运行结果。正式报告应分别保留首次运行完成率和完成队列结果。

## 工具版本与轮次

`coarse_feedback_tools`：早期引用范围诊断和 `contract_error_details_v1`。去澄清组 round_21 中 1992:0 正常完成为 refuse，3217:2 仍未决。

`contract_feedback_v2_tools`：增加公开措辞、空绑定和用户选择保留反馈。去剪枝组 round_21 的五条仍未完成，原始结果完整保留。

`contract_feedback_v3_tools`：多选诊断直接引用 `input.event.selected` 的确认依赖，并同时解释非输出角色继承和输出连接要求。独立恢复控制器 `recover_ablation_contract_failures.py` 只处理当前仍未完成的去澄清、去剪枝请求，最多追加三轮；持续失败转入原因复核，不能自动视为拒绝。

`prior_error_feedback_tools_v1`：新一轮尝试的初始粗拆分、分析输入中包含该请求上一轮的公开校验错误。只复制错误字符串，不复制模型答案、模拟用户私有意图或 Gold。该恢复会改变初始调用的输入，必须与完全相同输入的重试分别记录。去澄清、去剪枝两组剩余四条在该轮正常完成；两组最终均为 2,800 条真实 deliver/refuse。

串行组首轮仍在运行期间，以 `contract_feedback_v3_tools` 重试首轮已明确失败的 36 条，写入独立 `round_02`。未完成首轮样本不加入该队列，首轮 trace 保持不变；该队列与首轮剩余请求不重叠。启动证据为 `SERIAL_OVERLAPPED_RECOVERY_LAUNCH.json`。总共仅运行两个前端工作进程，调度器后续仍按首个真实终态选取。

## 复核入口

- `CONTRACT_RECOVERY_STATUS.json`：恢复控制器、子进程 PID、轮次和状态。
- `CONTRACT_RECOVERY_V3_LAUNCH.json`：本次调度启动记录。
- `<arm>/retries/round_XX_selection_proof.json`：选择 ID、worker 哈希及反馈适配器哈希。
- `<arm>/retries/round_XX_recovery_launch.json`：恢复轮次命令、源代码哈希和进程记录。
- `<arm>/retries/round_XX/`：未经改写的请求 trace 与导出行。

监控使用 `scripts/monitor_four_arm_terminal_cohort.py --collect`。完成证据必须是当前存活进程或已落盘的完整 trace，不能仅依赖启动文件。

## 串行调度中断恢复

旧调度器仅合并整轮结束后导出的 evaluation_rows，没有合并已完成单条 trace。进程中断后，round_03 因此误选了全部 2,800 条。核实后停止该孤立任务，保存 SCHEDULER_RECOVERY_INCIDENT.json；已有 trace 不删除。后续选择仍保留按轮次排序的首个真实终态，round_03 重复终态不能替换此前结果，重复调用成本照计。

resume_serial_terminal_safely.py 先调用原导出器，仅导出具有 ablation_sources_unchanged 和最终 ablation 标记的完整 trace。随后按同一选择规则排除已完成请求，将剩余 ID 划分为两个互不重叠的队列。round_04 / round_05 的合计选择为 2,414 条：2,402 条未运行以及 12 条仍未决；此前 386 条真实终态不再选入。

接续调度使用 nohup、独立会话、断开标准输入并忽略 SIGHUP；若已有工作进程存活，则等待它们结束，不能重复启动同一队列。SAFE_SERIAL_NOHUP_LAUNCH.json、SAFE_SERIAL_RESUME_STATUS.json 和 SAFE_SERIAL_CYCLE_XX.json 保存过程证据。该恢复最多自动进行四轮，仍未完成则转入原因检查，不能强制改标。前端全部完成后才接续冻结的后端及评测流程。

## 后端接续与最终指标入口

前端完整的去剪枝、去澄清两组通过 run_completed_ablation_backends_early.py 提前运行原后端和原 SQL 评分器。先核对 2,800 个 ID、所选 trace、澄清文本和预检查输入哈希；仅当 trace 与输入均一致时复用历史 SQL。去剪枝复用 1,210 条、去澄清复用 1,729 条。已存在的正式输入必须与其来源哈希一致，后续总调度不能重新清空 checkpoint。

原始 sql_results.jsonl 保持不变。权限未决通过数据库原生编译和依赖检查补充认定，保存原 SQL 哈希及证据；无效 SQL 单独记录，不能当作合法成功。去剪枝组 native_permission_no_pruning_early/ 已复核全部 88 条原始权限未决，无剩余复核错误。最终三组仍统一运行 native_permission_final/ 审计。

主文的需求保留结果采用 strict_ordered_evaluation/：冻结 1,398 条 denied，77 条正容量（50 条部分、27 条全部合法），133 个合法输出列。评分要求固定联合输出列、完整元组及重复次数，并遵循 Gold 要求的行序。deepseek_flash/<arm>/result_completion/ 属于此前 80 条容量口径的辅助评测，不能替代这一正式结果。最终表由 summarize_four_arm_terminal.py 在全量完成与审计通过后生成。

## 调度补位与中断成本

resume_serial_terminal_safely.py 在有一个工作槽空闲时，保留所有存活进程的完整选择集合，并排除已出现真实终态的请求；优先给未尝试请求分配最多 64 条的新批次。工作进程不超过两个，补位记录在 round_XX_recovery_launch.json，调度启动记录在 SAFE_SERIAL_REFILL_LAUNCH.json。原有分片保持运行，样本分母不变。

调用成本同时保留完整失败尝试与中断快照中已记录的 usage。尚未写回 usage 的在途调用用量标为未知，不填零，也不计决策成功。SERIAL_STALE_REMOVAL_DIAGNOSIS.json 保存两条旧暂存事件与后续有效回答衔接失败的只读证据；该文件本身不改变结果或运行源码。

## 去澄清辅助评测恢复完成

原始 SQL 评分共 1,806 条，Gold 执行错误为零。其后辅助 result_completion 的进程池工作进程异常退出；COMPLETION_RECYCLE_PROTOCOL.json 保存副本源码哈希及唯一修改：ProcessPoolExecutor 增加 max_tasks_per_child=1，使工作进程每个问题簇后回收。评分函数不改，已保存的问题簇直接复用。COMPLETION_RECOVERY_STATUS.json 已确认完成 476/476 个问题簇。旧失败记录保留作历史证据，当前 PIPELINE_STATUS.json 与 EARLY_BACKEND_STATUS.json 均为 complete。

去澄清组原始权限未决 258 条由 native_permission_no_clarification_early/ 完成复核：53 条 authorized、30 条 unauthorized、175 条 invalid_sql，无剩余复核错误。逐条 SQL 哈希与原始候选一致；无效 SQL 不计成功。

## 已完成两组的严格联合评分与复现

`score_completed_arms_ordered_early.py` 对去澄清、去剪枝分别核验同一批 2,800 个真实终态及后端任务覆盖，然后使用冻结的严格评分器。`strict_ordered_completed_arms/` 只包含 1,398 条原始 denied 的需求保留专项结果，每组 1,398 行、合计 2,796 行；这不是前端实验少跑了四条。全量行为与执行指标仍分别以 2,800 条队列及其规定子集计算。

本轮 476 个原始问题簇均已执行；1,398 条参考合法输出集合全部完成认证，正容量 77 条（50 条部分、27 条全部合法）。最终 2,796 个候选评分无未决。`strict_completed_arms_replay.log` 记录再次运行同一入口后的完成状态，`STRICT_COMPLETED_ARMS_REPLAY_LAUNCH.json` 记录复现启动信息。

三条额外输出对应关系复核保存在 `STRICT_NEW_SOURCE_CORRESPONDENCE_REVIEWS.jsonl`，按 Gold SQL 和候选 SQL 的精确 SHA256 绑定，仅确定候选输出对应哪个 Gold 输出，不宣称 SQL 等价，也不改写候选 SQL。最终正确性仍由原生执行、联合元组、重复次数、规定行序及权限门控决定。复核方式如实记录为 Codex 源码复核，复核时可见方法标识，不称为独立人工盲审。

两组提前评分与最终全组评分入口均自动合并这些精确哈希绑定的复核记录。`STRICT_SOURCE_REVIEW_PROMOTION.json` 保存最终入口修改前后哈希。当前两组统计属于阶段性结果；正式显著性校正必须等待串行组完成，统一按每个指标的三个消融对比做 Holm 校正。

## 2026-11-07 串行组剩余未决恢复

恢复监控时，原调度已结束，2,800 条均已尝试，2,776 条为真实终态（1,467 deliver、1,309 refuse），24 条 unresolved，技术错误为零。当前进程检查无存活前端后，按既有 `prior_contract_errors_v1` 协议将这 24 条划为两个互不重叠的 12 条队列，保存为 round_57、round_58。只从各自上一条失败 trace 复制公开校验错误，并保存 trace SHA256、反馈文件、工具源码哈希和命令；不使用 Gold 或评测反馈。此轮初始输入增加了错误诊断，按错误反馈恢复记录，不能称作完全相同输入的重试。

`SERIAL_PRIOR_ERROR_RECOVERY_LAUNCH.json` 保存两分片启动证据。`SERIAL_POST_PAUSE_RESUME_LAUNCH.json` 保存接续调度器启动证据。接续程序等待这两个已有工作进程，保留首个真实终态，重新导出完成 trace，再决定是否继续定向重试或进入原后端和严格评分。所有尝试和成本保留。

后续四轮恢复进行到 round_65/66 时，已完成两组另行通过所选 trace 的 ID、哈希、权限输入和消融边界审计，重试成本按全尝试记账。新增 `resume_serial_prior_feedback.py` 仅负责调度已有 `prior_contract_errors_v1` 工具：先等待现有串行控制器及其工作进程结束，再对仍未完成的 ID 做最多两轮、每轮两个互不重叠分片。只复制各自上一条公开错误，不改方法、验证器和选择规则。该输入变化与普通重试区分记录，不能把未决直接改成拒绝；没有剩余未决才接续后端。`SERIAL_PRIOR_FEEDBACK_LAUNCH.json`、`SERIAL_PRIOR_FEEDBACK_STATUS.json` 与各轮 `_prior_error_launch.json` 保存证据。

round_67/68 恢复三条，round_69/70 恢复一条后，串行组为 1,476 deliver、1,318 refuse、6 unresolved。所选 2,800 条 trace 的串行边界审计无异常。对剩余失败核查后，新增 `contract_error_details_v4`：只在原 `_contract_retry` 的 `contract_error` 中解释所有权去重后空子节点、依赖继承、跨父节点决策和内部主体误声明为输出等已有校验规则。诊断来自当前公开输入与上一条模型响应，不使用 Gold、私有用户输入、评测标签或分数；不修改响应或验证器。局部检查验证原输入、上一条响应均不变，非重试阶段不改变。`resume_serial_contract_v4.py` 对仍未完成 ID 做两轮有界恢复，原冻结方法保持一致；此为另一项明确记录的诊断输入变化，不称为完全相同提示词。启动证据为 `SERIAL_CONTRACT_V4_LAUNCH.json`。

round_71/72 恢复一条 refuse，round_73/74 恢复一条 deliver，累计 2,796 个真实终态，剩余四条。`SERIAL_FINAL_FOUR_DIAGNOSIS.json` 保存精确失败 trace 哈希。`SERIAL_RESIDUAL_FROZEN_REPLAY.json` 在无模型调用、无状态改写的条件下重放 3236:L1 的冻结调度器：剪枝后确有 field/operation 两个未答决策，下一步为 ask。因此不能仅去掉 unresolved 标记就认定交付，也不能在剪枝后追加用户对话而仍声称串行边界不变。第二次原 v4 协议恢复另存 `SERIAL_CONTRACT_V4_SECOND_PASS_LAUNCH.json`，只选择四条未完成请求，继续保留全部尝试。

round_75–78 后四条仍未决。新增 `bounded_validation_recovery_v1` 只扩展校验修正预算：原 validated_call 的首次生成和一次修正均失败后，最多追加四次相同验证器下的公开错误反馈修正；模型答案不作程序修改，验证器不放宽，模拟用户的校验次数不扩展。此为已记录的恢复预算变化，不能声称与历史完整方法预算完全一致。局部验证覆盖多次修正成功和达到上限仍拒绝无效声明两个路径。新增工具位于 `contract_feedback_v5_tools/`，部署与命令哈希保存在 `SERIAL_CONTRACT_V5_LAUNCH.json`。所有追加调用保存在 trace 并计入成本。

实际运行核验修正：v5 的 analysis.validated_call 补丁被方法安装时的 output boundary 覆盖，round_79/80 没有产生 recovery_attempt 调用。该轮三条真实恢复应归因于既有 v4 提示及重新采样，不能归因于扩展校验次数；`SERIAL_CONTRACT_V5_FIRST_CYCLE_AUDIT.json` 记录额外调用数为零。round_81 后仅 3604:L0 未决。v6 改为在实际 `output_contracts.validated_output_call` 边界追加修正；每次修正均先执行原 compile_output 形状验证，再执行同一状态验证器。已在完整 FourArmMethod.installed() 环境验证四次调用、额外修正可见及无效输出仍拒绝。部署记录为 `SERIAL_CONTRACT_V6_LAUNCH.json`；只恢复最后一个未完成 ID。


## V7 主体提供者诊断（2026-11-07）
V6 第82、83轮仍有3604:L0未决：模型把subject claim留在output节点，未满足原编译器要求的同parent非relation scope提供者。V7仅在contract_error中枚举公开subject_obligations及本次响应的匹配节点，解释原有规则；不改输出、验证器、权限或用户回答，沿用V6四次额外纠正预算。该恢复属于公开错误反馈协议，所有尝试和费用保留。

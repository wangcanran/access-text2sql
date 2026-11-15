# ACCESS：权限约束下的用数意图澄清

本仓库整理了主方法、基线、消融、数据构造与评测代码，以及离线复算结果的支撑材料。ACCESS 位于自然语言请求与 Text-to-SQL 模型之间，维护同一份需求状态，通过权限感知澄清和局部更新明确需求，在删除受限目标前检查依赖闭包。

## 快速检查

从仓库根目录执行，建议使用独立 Python 3.11 环境：

~~~bash
python -m pip install -r requirements.txt
python tools/reproduce_tables.py --out runs/tables
python tools/run_frontend.py --method access --fixture examples/synthetic_sales --out runs/demo --check-only
~~~

第一条实验命令复算 20 组主表结果及 3 组消融，与服务器审计结果逐项核对；第二条检查三个合成示例和源码锁。均不调用模型。真正运行模型需配置环境变量并去掉 --check-only。

## 怎么找代码

- 主方法在 clarification_useful_residual_v6/，依赖层和底层运行时保留原目录名。
- 基线、消融与 SQL 模型入口在 scripts/；统一入口是 tools/run_frontend.py。
- 数据构造在 data_generation/，生成与审核为不同阶段。
- 历史评测源码在 archive/evaluation/，离线表格复算在 tools/reproduce_tables.py。
- supplementary/metrics/ 保存逐请求分数与安全处理证据。
- extensions/p_expansion/ 是投稿后的扩充实验，不属于投稿主表。

本目录是独立发布包。数据库、模型权重、密钥、原始响应日志与旧实验输出未纳入。完整论文数据和授权参考需另行准备，合成示例不能替代论文数据。

[运行说明](docs/RUNNING.md) · [数据与指标](docs/DATA_AND_METRICS.md) · [版本来源](docs/PROVENANCE.md) · [第三方许可](THIRD_PARTY_NOTICES.md)

## 论文支撑材料

[查看补充材料 PDF](supplement.pdf)。

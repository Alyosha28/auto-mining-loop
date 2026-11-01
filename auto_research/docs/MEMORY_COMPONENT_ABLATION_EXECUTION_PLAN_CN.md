# Auto-Research 累计记忆组件消融执行文档

状态：执行完成；12/12 配对单元完成；全部协议检查通过；`development-panel null`
日期：2026-10-30..2026-10-31
协议版本：V002
历史实验：R1–R4、GEPA 与诊断 continuation V001 的结果保持不变

## 1. 研究目标

检验结构化累计研究记忆能否帮助模型在新任务上产生更好的冻结 harness，并区分以下效果：

- 累计记忆相对纯标量反馈的增量；
- 标量反馈与累计记忆组成的完整研究流程相对 fixed-harness 的表现；
- 逐候选诊断在累计记忆之上的新任务复现效果。

V001 的核心结果 `development-panel null` 保持不变，不通过追加旧面板 seeds、任务或预算寻找正结果。

## 2. 证据边界

- 市场数据限定为 `2017-01-01..2020-11-30`。
- `2020-12` embargo 不读取。
- 已读取的 2021–2023 与 2024–2025 窗口不再作为研究输入。
- 新任务与既有任务使用不同初始 harness、研究缺陷或种子池。
- 本实验属于 fresh-task development-panel evidence。
- 新任务仍来自开发窗，不构成独立市场时期 OOS。
- 不调用生产 score/admission，不向模型发送原始行情或封窗内容。

## 3. 冻结参数

- 任务数量：`4`，编号 T5–T8。
- 每任务 seeds：`11、29、43`。
- 每模型臂决策数：`2`。
- 每模型臂模型调用上限：`2`。
- 每次决策候选预算：`12`。
- 每次模型调用输出上限：`8000 tokens`。
- 每臂输入 token 上限：`120000`。
- 每臂输出 token 上限：`16000`。
- 模型：`kimi/k3.1-pro[1m]`。
- CLI effort：`low`；温度为 CLI 默认值。
- 工具与会话：工具禁用，会话不持久化。
- 有界修复：失败后使用剩余预算，不增加决策或调用次数。
- 每单元模型臂数量：`3`。
- 每单元模型调用上限：`6`。
- 全面板模型调用上限：`72`。
- 全面板候选位置上限：`864`。
- 全面板输出 token 上限：`576000`。

参数在模型调用前冻结。观察 select 后不修改当前版本的主指标、任务集合、seeds、预算或一致性规则。

## 4. 实验臂

### 4.1 fixed-harness

- 不调用模型。
- 使用任务初始 harness。
- 仅在所有模型臂冻结后进入共同样本比较。

### 4.2 scalar-only

- 从任务初始 harness 与逐 seed 初始安全 trace 开始。
- 接收当前 harness、标量 probe、kept edits 与可编辑表面。
- 历史记忆文件为空。
- 不接收逐候选诊断。
- 独立执行两次决策。

### 4.3 scalar + memory

- 与 scalar-only 使用相同初始状态和标量反馈。
- 额外读取结构化累计研究记忆。
- 记忆包含历史 harness、编辑、probe 结果、失败状态、来源与适用范围。
- 不接收逐候选诊断。
- 独立执行两次决策。

### 4.4 scalar + memory + diagnostic

- 与 scalar + memory 使用相同历史记忆。
- 额外接收当前轨迹的逐候选提案、行为指标与失败明细。
- 独立执行两次决策。

## 5. 主要与辅助估计量

主要估计量：

```text
scalar + memory champion select mean_abs_ic
− scalar-only champion select mean_abs_ic
```

辅助估计量：

```text
scalar + memory champion − fixed-harness
scalar-only champion − fixed-harness
scalar + memory + diagnostic champion − scalar + memory champion
scalar + memory + diagnostic champion − fixed-harness
```

主要问题为累计记忆增量。诊断比较用于新任务复现，不改写 V001 的结论。

## 6. 新任务面板

### 6.1 T5：高换手候选池

- 初始状态与 V001 任务不同的 crossover 槽位配置。
- 研究问题：在高换手候选池上，能否提高共同样本平均绝对 IC，并保留换手诊断？
- 主要指标：`mean_abs_ic`。
- 诊断：`turnover_proxy`、候选类型、coverage、duplicate_rate、novelty。

### 6.2 T6：低多样性候选池

- 初始状态使用不同的无 rank、无 crossover 配置。
- 研究问题：在低多样性候选池上，能否提高共同样本平均绝对 IC？
- 主要指标：`mean_abs_ic`。
- 诊断：novelty、duplicate_rate、pass_rate、coverage。

### 6.3 T7：头部候选不足

- 初始状态使用不同的无 rank、高 crossover 配置。
- 研究问题：在头部候选表现不足的初始状态下，能否提高候选池共同样本平均绝对 IC？
- 主要指标：`mean_abs_ic`。
- 诊断：`top_ic_mean`、候选 IC 分布、pass_rate、duplicate_rate。

### 6.4 T8：长周期种子池

- 初始种子池引入 `momentum_60` 与 `volatility_60`。
- 研究问题：在长周期种子池上，能否提高共同样本平均绝对 IC，并保留覆盖和年度稳定性诊断？
- 主要指标：`mean_abs_ic`。
- 诊断：coverage、`year_ic_min`、`icir_p25`、novelty、duplicate_rate。

所有任务在模型调用前使用真实固定 harness 的 probe-only 评价确认初始状态，筛选不执行 select。

## 7. 累计记忆契约

记忆臂与诊断臂读取同一个 `shared_research_memory.json`。记忆记录来自真实历史 run，包含：

- `source_run`；
- 历史任务或缺陷类别；
- 执行状态；
- 历史 harness 或编辑；
- 历史 probe 指标；
- 适用范围与证据边界。

禁止包含：

- 新任务在同一起始状态下的答案；
- 当前实验 select/holdout 指标；
- 原始行情、股票符号或逐日市场行；
- 生产 score/admission 结果；
- 没有来源的自然语言结论。

scalar-only 使用相同 schema 的空记忆文件。记忆干预从第一次决策开始，因此三个模型臂不使用 V001 的共享分支点。

## 8. 探索、champion 与 select

- 每个模型臂使用单条活动谱系。
- 活动分支可以继续负增量或未通过采纳契约的已执行候选。
- champion 只能在共同 probe 支持上相对当前 champion 满足任务契约时更新。
- 普通预测改进任务不启用通用 novelty、duplicate_rate 或 turnover 硬门禁。
- 每轮内部只执行 probe，不执行 select。
- 两次决策结束后，每臂最多一个 champion 进入共同 select。
- select 不进入 proposer，不用于更换 champion。
- 模型输出失败保留真实用量，并在剩余预算内继续预定决策。
- 未开始执行的候选不消耗实际候选位置。

## 9. 实验单位与支持条件

核心证据单位为任务与 seed 的配对结果，共十二个单元。

development-panel supported 需要同时满足：

1. 十二个 memory-minus-scalar 差值的中位数大于 `0`。
2. 至少三个任务各自的 seed 中位差值大于 `0`。
3. 每次排除一个任务后，其余任务的总体中位差值均大于 `0`。
4. 共同支持、split、初始身份、记忆契约、预算与冻结检查全部通过。
5. 不删除失败、零差值或负差值。

未满足时记录 `development-panel null`。协议检查失败时记录 `protocol-invalid`。这些规则属于开发面板一致性判断，不等同于统计显著性或独立 OOS 证明。

## 10. 敏感性与成本报告

- 全部十二个主要差值。
- 每个任务的三个 seed 差值与中位数。
- 留一任务中位数。
- fixed-harness 与诊断臂辅助比较。
- coverage、pass_rate、novelty、duplicate_rate、turnover_proxy 变化。
- `mean_daily_abs_ic_delta` 的连续日期块分布与集中度。
- 实际模型调用、输入/输出 tokens、候选位置、失败与恢复记录。

## 11. 实施与运行

复用：

- `run_research_loop.py`：固定两次决策、活动分支、champion、模型用量与预算恢复。
- `run_route_step.py`：parent/diagnostic 输入契约与共享记忆。
- `run_evo_step.py --probe-only`：候选执行和 probe-only 活动分支选择。
- `compare_harnesses.py`：冻结后的共同 probe/select 比较。
- `diagnostic_results.py`：日期块诊断与中位数计算。

新增：

- `config/memory_component_ablation_v002/`：协议、任务、初始 harness、记忆与初始 trace。
- `run_memory_component_ablation.py`：四臂单元执行与十二单元汇总。
- `MEMORY_COMPONENT_ABLATION_EXECUTION_PLAN_CN.md`：本执行文档。
- 新回归与真实 probe-only 任务筛选。

任务与 seed 单元可以受控并行，同一输出目录不得并发。单元内部保持固定顺序和单链，不增加模型或候选预算。

## 12. 结果目录与完成条件

结果写入新的目录：

```text
auto_research/agents/runs/MEMORY_COMPONENT_PROTOCOL_V002/
auto_research/agents/runs/MEMORY_COMPONENT_<task>_<seed>_V002/
auto_research/agents/runs/MEMORY_COMPONENT_RESULT_V002/
auto_research/agents/runs/MEMORY_COMPONENT_RESULT_V002_EXECUTION_SUMMARY_001/
```

完成条件：

- 四个任务与三个 seed 的十二单元全部保留结果；
- 三个模型臂的实际决策与调用次数符合预算；
- 所有共同支持和信息隔离检查完成；
- 主要、辅助、行为与成本结果汇总完成；
- 失败、零差值和负差值完整保留；
- 历史 run 不改写，当前没有 untouched 独立 OOS。

## 13. 完成结果

### 13.1 主要估计量

十二个 memory-minus-scalar select `mean_abs_ic` 差值如下：

- T5：seed 11 `+0.0062649974`，seed 29 `−0.0025923956`，seed 43 `+0.0090765831`；任务中位数 `+0.0062649974`。
- T6：seed 11 `+0.0101487023`，seed 29 `0.0`，seed 43 `+0.0132598853`；任务中位数 `+0.0101487023`。
- T7：seed 11 `−0.0161030279`，seed 29 `−0.0011484275`，seed 43 `+0.0054547897`；任务中位数 `−0.0011484275`。
- T8：seed 11 `−0.0015274920`，seed 29 `+0.0024479120`，seed 43 `−0.0009005130`；任务中位数 `−0.0009005130`。

全部差值包含六个正值、一个零值和五个负值，总体中位数为 `+0.0012239560`。排除 T5、T6、T7、T8 后的中位数分别为 `0.0`、`−0.0009005130`、`+0.0024479120`、`+0.0054547897`。

T5 与 T6 的任务中位数为正，数量低于预定的三个任务；排除 T5 与排除 T6 后的中位数也未达到正数条件。最终证据状态为 `development-panel null`。总体中位数为正，任务一致性条件未通过，当前结果不支持累计记忆在 fresh-task development panel 上具有稳定增量。

### 13.2 辅助比较

- memory 减 fixed：中位数 `+0.0094226906`，最小值 `0.0`，最大值 `+0.0322037726`。
- scalar 减 fixed：中位数 `+0.0104107660`，最小值 `+0.0017645277`，最大值 `+0.0189438873`。
- diagnostic 减 memory：中位数 `0.0`，最小值 `−0.0261706789`，最大值 `+0.0113143609`。
- diagnostic 减 fixed：中位数 `+0.0103639573`，最小值 `0.0`，最大值 `+0.0322037726`。

模型驱动臂相对 fixed-harness 保留了开发面板增益。累计记忆没有超过 scalar-only 的预定一致性标准，逐候选诊断也没有在累计记忆之上形成稳定增量；V001 的 `development-panel null` 结论保持不变。

### 13.3 行为与日期块诊断

memory 减 scalar 的行为差值：

- coverage：中位数、最小值和最大值均为 `0.0`，共同支持保持不变。
- pass_rate：中位数 `0.0`，范围 `−0.0833333333..0.0`。
- novelty：中位数 `+0.0333333333`，范围 `−0.125..+0.4`。
- duplicate_rate：中位数 `+0.0303030303`，范围 `−0.0151515152..+0.1060606061`。
- turnover_proxy：中位数 `−0.0072579147`，范围 `−0.0175151202..+0.0044146236`。

主要日期块诊断使用 `mean_daily_abs_ic_delta`，与候选池主指标分开。十个具有正日期块增量的单元中，最大正日期块占全部正日期块增量比例的中位数为 `0.2233214420`，最大值为 `0.3939976946`。诊断减记忆的对应统计覆盖七个单元，中位数为 `0.1882478371`，最大值为 `0.2810165996`。

### 13.4 成本、失败与恢复

- 有效 Kimi 调用：`72/72`；有效模型决策：`72`。
- 输入 tokens：`1,309,232`；输出 tokens：`277,654/576,000`。
- 原始候选预算记账：`840/864`；真实执行候选位置：`732`；真实执行候选决策：`61`。
- 失败或无效决策记录：`13`，均保留在单元与汇总结果中。
- T5/seed 43 与 T6/seed 43 各有一次错误模型通道尝试，身份检查在候选评价前拒绝；两次尝试不计入 72 次有效 Kimi 调用或研究效果计算。协议与 `channel_pin.json` 已显式固定 `kimi/k3.1-pro[1m]`。
- T7/seed 43 的公共比较在 `common_comparison_RECOVERY_001` 完成，原不完整目录保留。
- T8/seed 43 的准备阶段从已写入且内容一致的部分文件恢复，零字节文件按原输入补齐，没有增加模型调用或改变研究预算。
- 所有共同支持、split、初始身份、记忆契约、诊断契约、冻结 champion、内部 select 排除和任务约束检查通过。
- `score_window_read=false`，`independent_real_data_oos=false`。

完整结果位于 `agents/runs/MEMORY_COMPONENT_RESULT_V002/result.json`，执行摘要位于 `agents/runs/MEMORY_COMPONENT_RESULT_V002_EXECUTION_SUMMARY_001/result.json`。

### 13.5 验证

项目虚拟环境中的十个回归模块共 `107` 项测试全部通过，覆盖 research loop、route choice、harness evolution、V001、V002、proposer、GEPA、项目 skills、研究治理与本地模型运行时。`git diff --check` 通过。

# Auto-Research 核心诊断对照执行文档

状态：V001 已完成；§2 参数与任务配置已冻结；证据状态为 development-panel null
日期：2026-10-30
适用范围：开发窗口内的诊断反馈研究
历史实验：R1–R4、GEPA 既有 run 与结论保持不变

## 1. 研究目标

### 1.1 核心问题

从完全相同的探索分支点出发，在相同模型、预算、候选生成器和评价器下，增加真实执行诊断的 proposer，能否比继续接收标量反馈的 proposer 产生更好的冻结 harness？

### 1.2 干预内容

diagnostic continuation 相对 parent-only continuation 增加的是一个诊断信息包，包括：

- 逐候选 probe 指标；
- 候选执行失败类别；
- coverage、pass_rate、turnover_proxy、novelty、duplicate_rate；
- 当前谱系中的执行成本与失败摘要。

核心结论只归因于该诊断信息包，不分别归因于记忆、行为多样性、失败归因或成本反馈。

### 1.3 证据边界

- 全部市场数据限定为 `2017-01-01..2020-11-30`。
- `2020-12` embargo 不读取。
- 已读取的 2021–2023 与 2024–2025 窗口不再作为研究输入。
- 当前实验属于 development-panel evidence。
- seed 改变的是开发窗内部日期分块，不构成独立市场时期。
- 当前没有 untouched 独立 OOS，不形成生产有效性结论。
- 不调用生产 score/admission，不向模型发送原始行情或封窗内容。

## 2. 待用户修改的冻结参数

- 任务数量：`4`，见 §5。
- 每任务 seeds：`11、29、43`，见 §6。
- 共享探索决策数：`2`，见 §7.1。
- 每 continuation 决策数：`2`，见 §7.2。
- 每次决策候选预算：`12`，见 §7.3。
- 每次模型调用输出上限：`8000 tokens`，见 §7.3。
- 每阶段输入 token 上限：`120000`，见 §7.3。
- 每阶段输出 token 上限：`16000`，见 §7.3。
- 有界修复次数：`1`，见 §7.4。
- 核心 continuation 并行分支数：`1`，见 §8.1。
- 主要估计量：12 个配对单元的 select `mean_abs_ic` 差值中位数，见 §10.1。
- 面板一致性规则：至少 3 个任务的 seed 中位差值大于 0，见 §10.2。
- 留一任务敏感性规则：每次排除一个任务后，其余任务的中位差值均大于 0，见 §10.2。

修改规则：

- 所有参数必须在模型调用前冻结。
- 核心 continuation 的两臂必须使用相同决策次数、模型调用上限和候选预算。
- 运行后如需修改参数，建立新的协议版本，保留原协议与已完成 run。
- 已观察 select 结果后，不修改当前版本的主指标、任务集合、seeds 或一致性规则。

## 3. 实验臂

### 3.1 fixed-harness

- 不调用 proposer 模型。
- 使用初始 harness 直接生成和评价候选。
- 提供每个任务与 seed 的固定生成器参照。
- 不参与共享探索与 continuation。

### 3.2 parent-only continuation

- 从共享探索分支点复制。
- 读取研究任务、当前 harness、可编辑表面和标量任务反馈。
- 不读取逐候选诊断、失败类别或行为多样性明细。
- 执行固定两次 continuation 决策。

### 3.3 diagnostic continuation

- 与 parent-only 使用完全相同的共享分支点。
- 额外读取 §1.2 中定义的诊断信息包。
- 执行固定两次 continuation 决策。
- 模型、温度、可编辑表面、预算和评价器与 parent-only 完全一致。

### 3.4 核心比较

主要比较：

```text
diagnostic continuation − parent-only continuation
```

辅助比较：

```text
parent-only continuation − fixed-harness
diagnostic continuation − fixed-harness
共享探索起点 − fixed-harness
```

辅助比较用于解释模型探索和共享阶段的影响，不替代核心配对差值。

## 4. 评价契约分层

### 4.1 执行契约

所有合法研究假设均可进入真实小规模实验。硬约束仅包括：

- 数据窗口与信息边界；
- 可执行性与指标可计算性；
- 无未来信息泄漏；
- 明确的候选、模型调用、token 和决策预算；
- 原始市场数据只读；
- 失败、无效输出与负结果完整保留。

以下指标不阻止普通预测改进任务执行：

- `mean_abs_ic` 增量；
- novelty；
- duplicate_rate；
- coverage；
- pass_rate；
- turnover_proxy。

它们在探索阶段全部作为诊断记录。

### 4.2 对照契约

两臂差值成立必须满足：

- 相同共享分支点；
- 相同模型与生成参数；
- 相同 continuation 决策次数；
- 相同模型调用上限；
- 相同候选预算；
- 相同 evaluator；
- 相同 fit/probe/select 划分；
- 相同共同 `date+symbol` 支持；
- 在共同支持上重新排名并计算指标；
- select 不进入 proposer 输入。

共同支持、信息隔离和预算公平属于比较有效性要求，不限制假设内容。

### 4.3 任务采纳契约

约束随研究问题确定：

- **预测能力改进**：主要评价共同样本平均绝对 IC 增量及稳定性；硬约束由运行前的覆盖或可执行性要求确定；novelty、duplicate_rate、turnover_proxy 作为诊断。
- **多样性修复**：主要评价运行前指定的多样性指标；novelty 或 duplicate_rate 可作为硬约束；IC 与其他行为指标作为诊断。
- **稳定性修复**：主要评价 `icir_p25` 或年度稳定性；硬约束由运行前的最低稳定性要求确定；平均 IC 与其他行为指标作为诊断。
- **覆盖修复**：主要评价 coverage；硬约束由运行前的最低覆盖要求确定；IC 与多样性作为诊断。
- **换手控制**：主要评价 turnover_proxy；硬约束由运行前的换手上限确定；IC 与多样性作为诊断。

本执行文档的核心四任务均属于预测能力改进，不启用通用 novelty 硬门禁。

## 5. 研究任务面板
每个任务从为该任务冻结的当前最佳可用 harness 或明确活跃分支开始，该初始状态仍保留待研究缺陷。两臂共享当前 champion、历史实验成果、跨任务经验和失败记录，所有经验均附真实来源、probe 证据与适用范围。核心对照不注入当前任务在同一起始状态下的标准答案或指令式算子推荐，当前实验的 select/holdout 结果始终隔离。两个 continuation 的唯一输入差异是 diagnostic 臂额外获得当前分支的执行诊断信息包。
### 5.1 T1：资源分配缺陷

- 初始状态：crossover 或 mutation 槽位分配低效。
- 研究问题：重新分配固定候选预算能否提高候选池平均绝对 IC？
- 主要指标：`mean_abs_ic`。
- 诊断：候选类型分布、pass_rate、novelty、duplicate_rate、turnover_proxy、coverage。
- 同一起始状态下的 R2 有效编辑及其结果不进入 prompt；已纳入初始 harness 的历史成果、其他任务 probe 记录与失败记录按 §5 共享。

### 5.2 T2：候选重复缺陷

- 初始状态：roster 的 duplicate_rate 较高。
- 研究问题：在重复候选占比较高的初始状态下，能否提高候选池平均绝对 IC？
- 主要指标：`mean_abs_ic`。
- 诊断：duplicate_rate 及其候选对、novelty、coverage、pass_rate、turnover_proxy。
- novelty 与 duplicate_rate 不设全局不下降门禁。

### 5.3 T3：合格候选不足缺陷

- 初始状态：roster 的 pass_rate 较低。
- 研究问题：在合格候选占比较低的初始状态下，能否提高候选池平均绝对 IC？
- 主要指标：`mean_abs_ic`。
- 诊断：pass_rate、逐候选 IC/ICIR、coverage、novelty、duplicate_rate、turnover_proxy。
- pass_rate 不下降仅作为诊断，除非运行前明确加入该任务约束。

### 5.4 T4：年度不稳定缺陷

- 初始状态：年度 IC 或 `icir_p25` 较弱。
- 研究问题：在预测表现不稳定的初始状态下，能否提高候选池平均绝对 IC？
- 主要指标：`mean_abs_ic`。
- 诊断：`icir_p25`、年度 IC、日期块增量分布、coverage、novelty、duplicate_rate、turnover_proxy。
- 稳定性结果单独报告，不用平均 IC 改善替代稳定性改善。

### 5.5 每个任务的冻结文件

```text
research_question
initial_harness
initial_safe_trace
editable_surface
primary_metric
task_diagnostics
task_constraints
budget
seed_set
stop_rule
select_freeze_rule
```

## 6. 数据划分与实验单位

### 6.1 seeds

每个任务使用：

```text
11、29、43
```

seeds 只控制年度分层的连续日期块分配：

- fit：50%；
- probe：25%；
- select：25%。

所有实验臂共享完全相同的日期划分。

### 6.2 实验单位

核心证据单位：

```text
任务 × seed 的配对 continuation
```

总实验数量：

- 任务：4 个。
- 每任务 seeds：3 个。
- 共享探索轨迹：12 条。
- parent-only continuations：12 条。
- diagnostic continuations：12 条。
- fixed-harness 基线：12 个。
- 核心配对证据单元：12 个。

### 6.3 证据名称

- `development-panel supported`：满足 §10 的开发集支持规则。
- `development-panel null`：未满足开发集支持规则。
- `protocol-invalid`：共同支持、预算或信息隔离检查失败，不进入研究效果判断。

seed 间波动只表示开发窗分块敏感性。

## 7. 运行协议

### 7.1 阶段 A：共享探索

每个任务与 seed 先运行一条共享轨迹：

- 最多 2 次研究决策；
- 每次最多 12 个候选；
- 最多 2 次模型调用；
- 总输入 token 上限 120000；
- 总输出 token 上限 16000；
- proposer 只读取研究任务、当前 harness、可编辑表面与标量任务反馈；
- 所有合法假设均可执行；
- 完整行为指标保存到执行 trace，但不向共享阶段 proposer 提供诊断明细。

共享阶段输出：

- 当前 harness；
- 标量执行轨迹；
- 候选与编辑谱系；
- 实际模型与 token 用量；
- 完整但受访问控制的诊断结果；
- continuation 的只读分支点。

### 7.2 阶段 B：配对 continuation

从同一分支点复制 parent-only 与 diagnostic 两臂。

每臂固定执行：

- 2 次研究决策；
- 最多 2 次模型调用；
- 每次最多 12 个候选；
- 总输入 token 上限 120000；
- 总输出 token 上限 16000；
- 单次输出 token 上限 8000；
- 相同 evaluator、seed 和可编辑表面。

continuation 期间：

- 不启用提前停止；
- 不启用并行 FORK；
- 不允许把剩余预算转移到其他任务或 seed；
- 模型调用失败消耗一次调用预算；
- 无效输出、执行失败和负结果全部保留；
- 两臂均执行预定次数，保证主要对照的实际模型调用数一致。

### 7.3 每个任务与 seed 的总预算

- 共享探索：2 次决策、2 次模型调用、24 个候选位置、16000 个输出 tokens。
- parent-only：2 次决策、2 次模型调用、24 个候选位置、16000 个输出 tokens。
- diagnostic：2 次决策、2 次模型调用、24 个候选位置、16000 个输出 tokens。
- 合计上限：6 次决策、6 次模型调用、72 个候选位置、48000 个输出 tokens。

整个核心面板：

- 最多 72 次模型调用；
- 最多 864 个候选位置；
- 最多 576000 个输出 token；
- fixed-harness 不产生模型调用。

### 7.4 修复

- 每个模型研究单元最多一次有界修复。
- 修复目标必须来自真实执行错误。
- 修复不改变 evaluator、日期划分、主指标或任务约束。
- 修复使用的模型调用计入原定调用上限，不增加调用次数。
- 修复失败保留现场，研究单元按预定协议结束。

### 7.5 模型与输出

- 核心实验固定同一个模型，模型标识通过真实返回用量验证。
- 模型、温度、结构化输出协议与工具权限在运行前冻结。
- proposer 不读取原始行情。
- proposer 不读取 select 或生产评价结果。
- 模型只能提交可编辑表面内的修改。
- 无效提案保留原文、校验错误和实际用量。

## 8. 探索状态与 champion

### 8.1 核心实验的活动谱系

核心 continuation 使用单条活动谱系，不并行扩展分支。

允许：

- 负 IC 中间状态继续；
- novelty 或 coverage 下降的中间状态继续；
- 根据 probe 诊断进行有界修复；
- 保留未被采纳的编辑和候选。

核心 continuation 不允许：

- 并行 FORK；
- 通过多条候选路径取最大值；
- 根据 select 重新选择中间状态；
- 通过新增分支消耗额外预算。

### 8.2 champion_harness

`champion_harness` 是当前研究单元提交给冻结评价的 harness。

更新规则：

- 只能依据 probe 和任务采纳契约；
- 不能依据 select；
- 非采纳中间状态保留在活动谱系中；
- 每个研究单元最多提交一个 champion；
- 没有任何编辑满足采纳契约时，champion 保持该 continuation 的初始 harness。

### 8.3 active_branch

`active_branch` 表示当前继续实验的状态，可以包含尚未获得采纳支持的中间编辑。

它用于允许大胆假设继续执行，不代表已验证改进，不覆盖 champion。

### 8.4 状态标签

```text
EXPLORED
FROZEN_FOR_CONFIRMATION
DEV_SUPPORTED
NULL
INVALID
STOPPED
```

标签只描述执行与证据状态，不将单个候选的局部改善命名为整个 auto-research 有效。

## 9. 冻结与 select

### 9.1 冻结时点

每个 continuation 在两次决策结束、预算耗尽或协议失败后冻结：

- champion harness；
- 完整活动谱系；
- 实际模型、候选与 token 用量；
- 任务采纳判定；
- 未采纳编辑及原因；
- select 评价输入。

### 9.2 select 执行

- 每个冻结 champion 只评价一次。
- parent-only 与 diagnostic 使用共同样本比较器。
- select 结果不进入 proposer。
- 不根据 select 更换 champion。
- 不根据 select 追加 seeds、任务或预算。
- 未通过信息隔离或共同支持检查的比较标记为 `protocol-invalid`。

### 9.3 fixed-harness 评价

fixed-harness 使用同一 split、候选预算和 evaluator，在与相关比较臂一致的共同样本上评价。

## 10. 结果判定

### 10.1 主要估计量

对每个任务与 seed 计算：

```text
diagnostic champion 的共同样本 select mean_abs_ic
− parent-only champion 的共同样本 select mean_abs_ic
```

主要汇总：

```text
12 个配对差值的中位数
```

同时公开：

- 全部 12 个差值；
- 每个任务的三个 seed 差值；
- fixed-harness 参照；
- 两臂实际成本；
- 无效与失败记录。

### 10.2 development-panel supported 条件

以下条件全部满足：

1. 12 个配对差值的中位数大于 0。
2. 至少 3 个任务各自的 seed 中位差值大于 0。
3. 每次排除一个任务后，其余任务的总体中位差值均大于 0。
4. 各任务运行前指定的硬约束全部满足。
5. 核心配对比较的共同支持与冻结检查通过。
6. 正向结果不能依赖删除负差值、失败或无效运行。

这些条件属于预先约定的面板一致性规则，不等同于统计显著性证明。

### 10.3 development-panel null 条件

未满足 §10.2 时记录：

> 当前诊断信息包在该任务面板与固定预算下，没有显示相对 parent-only 的稳定开发集增量。

此时：

- 不追加 seeds 或任务寻找正结果；
- 不调整主指标或阈值；
- 不使用 R4 或 GEPA 的结果替代核心 null；
- 个别任务的局部正向结果仍按原数值保留。

### 10.4 敏感性与诊断

报告：

- 按连续日期块的 IC 增量分布；
- 增量是否集中于少量日期；
- 留一任务结果；
- coverage、pass_rate、novelty、duplicate_rate、turnover_proxy 的变化；
- 实际模型调用、输入/输出 token 和候选消耗；
- 各研究单元的结束原因；
- proposer 无效输出比例。

开发窗 seeds 与日期块的重采样只标记为开发集敏感性分析。

## 11. R4 与 GEPA 的独立定位

### 11.1 R4

R4 研究路线权限与预算分配，不进入核心诊断信息包主效应。

后续路线实验需固定：

- 分支数量；
- 分叉位置；
- 每分支预算；
- 单链完整预算对照；
- best-of-N 等总预算对照；
- 独立运行 seeds。

当前单决策 R4 null 结果继续保留，不外推到多步路线自治。

### 11.2 GEPA

GEPA 作为 proposer guidance 优化工具，单独回答文本优化问题。

有效对照需要：

- 相同 seed guidance；
- 相同 base harness；
- 相同任务与数据 split；
- 相同优化与评价预算；
- 冻结后共同样本评价；
- 明确的单运行或小预算证据限制。

`GEPA_LIVE_OPT_005` 的四次评价只用于记录该预算下的执行结果，不用于证明 GEPA 有效或普遍无效。

## 12. 实施工作包

### WP1：协议与任务配置

修改：

- `auto_research/docs/DESIGN_CN.md`
- `auto_research/README.md`
- 新增核心任务配置目录

交付：

- 四个任务的冻结配置；
- 共享探索与 continuation 的输入契约；
- 模型、预算、seeds 和主指标冻结记录；
- 包含共享结构化历史成果、当前任务无答案示例的中性 prompt。

### WP2：共享分支点

修改：

- `run_research_loop.py`
- `run_route_step.py`
- 必要的 trace 摘要代码

交付：

- 共享探索结束后生成只读分支点；
- 两臂从相同分支点恢复；
- parent-only 与 diagnostic 输入只存在诊断信息差异；
- continuation 使用固定决策次数；
- 模型调用失败正确计入预算。

### WP3：champion 与活动谱系

交付：

- `champion_harness` 与 `active_branch` 分离；
- 非采纳中间状态可继续执行；
- 非采纳状态不更新 champion；
- 每个研究单元最多一个 champion 进入 select；
- 所有状态与编辑保留真实谱系。

### WP4：结果汇总

交付：

- 12 个核心配对差值；
- fixed-harness 辅助比较；
- 任务与 seed 分布；
- 留一任务敏感性；
- 日期块集中度；
- 成本与失败汇总；
- `development-panel supported/null` 判定。

### WP5：验证

新增回归：

- 共享分支点的两臂身份一致；
- 除诊断信息外，两臂 prompt 与协议一致；
- 负 IC 中间状态可以继续执行；
- novelty 下降不阻止普通预测任务；
- 非采纳状态不会更新 champion；
- 每研究单元最多一个 select champion；
- continuation 两臂实际调用次数一致；
- 失败调用计入原定预算；
- 共同支持或 split 不一致时拒绝比较；
- select 不进入 proposer；
- 历史 R1–R4 与 GEPA run 不被改写。

运行相关测试和真实小调用，保留失败与 null 结果。

## 13. 运行与结果目录

建议使用新的版本化目录：

```text
auto_research/agents/runs/DIAGNOSTIC_CONTINUATION_PROTOCOL_<version>/
auto_research/agents/runs/DIAGNOSTIC_CONTINUATION_<task>_<seed>_<run>/
auto_research/agents/runs/DIAGNOSTIC_CONTINUATION_RESULT_<version>/
```

每个研究单元默认保留：

```text
config.json
result.json
必要 trace
champion harness
活动谱系
实际模型用量
恢复命令
```

不默认创建 claims、reviewer、snapshot 或完整审计链。

已有 run 不覆盖。协议变化写入新版本目录。

## 14. 核心结果模板

```json
{
  "protocol_version": "<version>",
  "model": "<verified-model-id>",
  "task_count": 4,
  "seeds": [11, 29, 43],
  "paired_units": [
    {
      "task": "<T1-T4>",
      "seed": 11,
      "shared_run": "<run>",
      "parent_run": "<run>",
      "diagnostic_run": "<run>",
      "common_support_verified": true,
      "select_diagnostic_minus_parent": 0.0,
      "actual_model_calls_equal": true,
      "task_constraints_satisfied": true,
      "status": "COMPLETED"
    }
  ],
  "median_paired_delta": 0.0,
  "tasks_with_positive_seed_median": 0,
  "leave_one_task_out_medians": {},
  "all_protocol_checks_passed": true,
  "evidence_status": "development-panel supported|null",
  "score_window_read": false,
  "independent_real_data_oos": false
}
```

## 15. 执行前检查

- [x] 用户已修改并冻结 §2 参数。
- [x] 四个任务的初始状态与诊断缺陷已确认。
- [x] 两臂共享的历史成果均有真实来源、probe 证据与适用范围；当前任务同一起始状态的标准答案与指令式编辑建议不进入 prompt。
- [x] parent-only 与 diagnostic 输入差异仅限于诊断信息包。
- [x] 模型身份和生成参数已冻结。
- [x] 两臂 continuation 决策数与预算一致。
- [x] 核心 continuation 无并行分支与提前停止。
- [x] 每个研究单元最多一个 champion。
- [x] select 执行规则已冻结。
- [x] 主估计量、支持条件与 null 条件已冻结。
- [x] 全部数据访问限定在授权开发窗。
- [x] 新 run 与协议版本目录已确定。
- [x] 历史结果保持不变。

## 16. 实验设计依据

- [ConflictGuide](https://arxiv.org/html/2609.39933)：共同探索后分叉、相同 continuation 预算，以及诊断反馈介入时机的控制。
- [Harness²](https://arxiv.org/html/2610.14224)：固定 evaluator、组件消融、多次独立运行与预算档位意识。
- [OO-Harness](https://arxiv.org/html/2610.28380v1)：discovery/held-out 分离、额外推理对照和冻结后评价。
- [Fork-and-Flush](https://arxiv.org/html/2610.07447)：固定分叉位置、固定分支数量，以及单链和 best-of-N 对照。
- [GEPA](https://arxiv.org/html/2507.19457)：train/validation/test 分离、相同基础系统与预算的优化器比较。
- [Beneath the Diff](https://arxiv.org/html/2609.00077v1)：代码表面差异与机制多样性的区分，避免将结构差异直接命名为研究创新。
- [Harmony Harness](https://arxiv.org/html/2610.15150)：多目标搜索与行为诊断的分离，避免将结构 novelty 设为通用准入条件。
- [AI Research Teams](https://arxiv.org/html/2610.28887)：记忆、角色背景和预算分配的独立消融，限定组合干预的归因范围。

## 17. V001 实际执行结果

### 17.1 配对差值

- T1：seed 11 为 `+0.0090344697`，seed 29 为 `0.0`，seed 43 为 `0.0`；任务中位数为 `0.0`。
- T2：seed 11 为 `0.0`，seed 29 为 `+0.0030610802`，seed 43 为 `+0.0105610213`；任务中位数为 `+0.0030610802`。
- T3：seed 11 为 `0.0`，seed 29 为 `0.0`，seed 43 为 `+0.0054597441`；任务中位数为 `0.0`。
- T4：seed 11 为 `+0.0073498587`，seed 29 为 `−0.0025213691`，seed 43 为 `0.0`；任务中位数为 `0.0`。

十二个差值包含五个正值、六个零值和一个负值。总体中位数为 `0.0`，正任务中位数数量为 `1`，四个留一任务中位数均为 `0.0`。全部协议检查通过，最终证据状态为 `development-panel null`。

### 17.2 实际成本与失败

- 模型调用：`72/72`。
- 输入 tokens：`1,051,893`。
- 输出 tokens：`307,483/576,000`。
- 原始候选预算记账：`852/864`。
- 从真实子执行记录计算的候选位置：`840`；完成候选执行的决策为 `70`，两条模型输出超限失败没有执行对应候选。
- 失败决策：`2`，均保留原始用量与现场。
- T3/seed 43 的 parent 第一次调用输出 `10248` tokens，超过单次上限；恢复 run 继承已消耗预算并完成第二次决策。parent 最终总计两次调用、`13823` 输出 tokens，未增加单元预算。
- 任务与 seed 单元采用受控并行，同一输出目录不并发；每个单元内部的单链、预算、评价与冻结顺序保持不变。

### 17.3 辅助比较与行为诊断

- parent-only 减 fixed-harness 的 select 差值中位数为 `+0.0130691008`，最小值为 `0.0`；十一个单元为正，一个单元为零。
- diagnostic 减 fixed-harness 的 select 差值中位数为 `+0.0158894872`，最小值为 `+0.0068559858`；十二个单元均为正。
- 共享 champion 减 fixed-harness 的 select 差值中位数为 `+0.0091944098`，最小值为 `0.0`。
- diagnostic 减 parent 的 coverage 差值在所有单元均为 `0.0`，符合共同支持评价。
- novelty 差值中位数为 `0.0`，范围为 `−0.1` 到 `+0.2916667`。
- duplicate_rate 差值中位数为 `0.0`，范围为 `−0.0303030` 到 `+0.1060606`。
- turnover_proxy 差值中位数为 `0.0`，范围为 `−0.0238880` 到 `+0.0362381`。
- 日期块正向集中度中位数为 `0.2525747`，最大值为 `0.4605387`；逐日与逐块数据保留在各单元结果中。

这些辅助结果表明模型驱动的共享探索与 continuation 在该开发面板中普遍超过 fixed-harness；核心诊断信息包的增量未达到预定面板一致性条件。所有证据仍属于 development-panel evidence，没有 untouched 独立 OOS。

完整结果：`auto_research/agents/runs/DIAGNOSTIC_CONTINUATION_RESULT_V001/result.json`。
执行摘要：`auto_research/agents/runs/DIAGNOSTIC_CONTINUATION_RESULT_V001_EXECUTION_SUMMARY_001/result.json`。
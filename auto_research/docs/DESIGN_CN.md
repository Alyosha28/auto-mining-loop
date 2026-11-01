# Auto-Research 架构：从证据生产回到研究进化

> 2026-10-30 状态：共同样本、失败恢复和 proposer trace 边界已落实；多 harness 比较器在真实 dev 新 run 精确复现 R2 seed 11 增量 `+0.0028168880264860946` 及原支持哈希。固定 Kimi K3.1 的真实 proposer smoke 已完成并记录实际 token 用量。R4 单决策对照中，local-only 提案无效、route-choice 未采纳编辑，seed 11/29 的 route 减 local select 主指标差值均为 0。GEPA 固定预算优化已完成，任务提案调用 3 次、反思调用 1 次、评价调用 4 次，最佳候选仍为空 guidance，没有采纳文本变更。现有证据均限于 dev，不构成独立 OOS、多步路线自治或反思优化有效性证明。

## 1. Retro：为什么原路线收窄

- **P1：目标替代。** 把可审计性当成了研究进展。claim、snapshot、guard 和论文检查很完整，实际搜索却主要是固定 grammar、小预算表达式变异。
- **P1：反馈过薄。** `agents/qd_search.py` 的提案 prompt 主要是 parent 和语法；模型看不到逐候选指标、失败轨迹和归因。外层会选择，模型却不会据此改变研究策略。
- **P1：证据层级混用。** `benchmark/bench/core.py` 的 force-gate QD 是 LLM-free 合成任务，不能作为真实 LLM auto-research 证据。
- **P1：复用不足。** 父项目已有 dev 候选生成、评价和 `factor-evolution-harness2` 的 LLM 两阶段编辑接口；最初方案却准备另起小 QD 和平行 R2。经用户提醒后，改为复用父项目 dev + harness²。
- **P1：默认流程错配。** 每次探索都被绑定到完整 claims、论文、meta 和主库检查，增加了与研究问题无关的成本。
- **P2：指导文件过时。** 两个 README 重复，agents 文档仍等待 Sage，旧 agenda/survey 没有历史标识。

修正不是取消验证，而是让验证服务研究。**不要繁琐的证明链与审计。** 不再把 prereg、claim、snapshot、reviewer、hash 链和完整 guard 作为每个探索步骤的前置条件，也不补造历史材料。

## 2. 研究问题

> 同一模型、同一预算、同一真实 dev evaluator 下，能读取执行轨迹并修改 research harness 的 agent，是否比固定 harness 发现更多可复现、机制多样的因子？

这里有两个不同层次，不能再混称：

1. **Candidate search**：发现因子表达式、程序或机制组合。
2. **Research-harness evolution**：改进提出假设、选择实验、使用记忆、诊断失败和分配预算的执行系统。

## 3. 目标闭环

```text
研究问题 / 既有经验
          ↓
LLM proposer → 可检验的 harness 假设与编辑
          ↓
父项目 dev worker + 确定性 evaluator
          ↓
精简 trace：指标、失败类别、机制差异、覆盖率、成本
          ↓
同一 proposer 读取诊断 → 下一假设 / 修复 / 分支 / 回退
          ↓
expression archive + harness lineage
```

关键是把已有组件接成反馈闭环，而不是另造平行系统。`factor-evolution-harness2` 已有两阶段 LLM 编辑和 Propose–Probe–Compose；R2 要补的是诊断驱动的连续迭代与父项目 dev 接入，不是再次实现一个 proposer。

## 4. 组件与复用边界

| 组件 | 复用底座 | 接入要求 |
|---|---|---|
| Dev worker | 父项目 `variant_series`、`screen_candidates`、`research/coverage_aware_search.py` 的确定性生成与评价 | 只调用纯 dev 函数；旧 `agents/qd_search.py` 降为历史 baseline |
| Data loader | harness² 的真实 dev loader 与日期参数 | 不调用 `run_v2_mining_batch_v2.load_panel` 或生产 CLI：它先读全表再筛 dev；实际读取范围必须限定在授权窗口 |
| Proposer | `run_evo_step.py` 的 prompt pack、`edits.json` 和校验接口 | 同一个 LLM 对比 parent-only / diagnostic 反馈；输出可检验的 harness 假设与编辑，不只生成公式 |
| Harness executor | `evo_harness.py` 的可编辑 surface、`evo_probe.py` 的 matched probe、compose | 不重写编辑/评价框架；`meta/meta_loop.py` 仅作通用文件 patch 的可选工具 |
| Memory | harness² 的 experience lessons、真实 run traces 与失败记录 | 让失败经验影响下一次提案；不先建大型知识图谱 |
| Archive | 表达式评价、harness 版本与本地 Git checkpoint | 区分机制/行为多样性与 AST 数量；源码可回滚，runs 不纳入 Git |

### 可变对象

- harness² 的 operators、seed/crossover/mutation 策略、probe 选择、预算与停止设计；
- 假设、prompt、角色与上下文组织；
- 失败记忆检索、实验选择与预算分配；
- 诊断 probes、工具适配、执行控制与研究 harness 代码。

若这些设计导致低覆盖、无效字段、低产出或过拟合，先在新 run/版本中修改并重跑同口径对照，不把第一次失败当终点。原配置、失败结果与恢复点必须保留。

### 冻结对象

- 原始数据、授权窗口和标签；
- 同一对照中的核心 evaluator 与主指标；
- 历史结果与失败记录。

若门禁或评价设计本身有问题，可创建新版本，但必须同时重跑基线与候选，不改写历史。模型权重训练不是当前前提；主要对照固定 worker/proposer 模型，避免把更强的外部 proposer 误认为 harness 改进。

## 5. 记忆与多样性

每个探索步骤只保留足够回答“下一步为什么这样做”的信息：

```text
question、候选或 patch、执行指标、失败类别、机制/行为摘要、成本、next action
```

- **Expression archive** 保存候选及其真实评价，不按语法多样性宣称机制多样性。
- **Harness lineage** 保存编辑、对应执行轨迹和后续效果，支持分支、修复与回退。
- harness² 的 experience lessons 可以复用，但必须能影响后续提案；只写入 ledger 而不再使用，不构成研究记忆。
- 分支用于探索不同机制路径，不等于简单增加 seeds 或 agent 数量。源码恢复点使用本地 Git checkpoint，不 push。

## 6. 分阶段实验，而不是一次性大重构

### R1：诊断反馈是否有效

在同一个 LLM、同一 seed pool、budget、editable surface、提案数量和 dev evaluator 下比较：

- A：base harness + parent/seed 上下文 + 编辑任务，不提供逐候选诊断或历史 lessons；
- B：A 的全部内容 + reference metrics、失败类型、机制/行为差异、成本和必要的历史 lessons。

**不能用 deterministic proposer 对 LLM proposer 来冒充诊断反馈消融。** 两臂只改变反馈内容，其他生成与评价协议一致。dev 主指标在运行前固定；null 与失败同样保留。

本轮真实执行为 `R1_PARENT_S11_IC_001` 与 `R1_DIAG_S11_IC_002`，同模型独立上下文、相同 base/reference/budget。最初 pass_count 主指标在提案前已饱和，故在任何模型编辑前新开 `R1_CONFIG_IC` 改用 probe 平均 |IC|，旧配置与 PREPARED 现场保留。两臂均只采纳 `rank`，主指标均为 `0.0436379 → 0.0510255`（`+0.0073876`），诊断相对 parent 的增量为 **0**。共享 prompt 含有 rank 示例，且真实 seed 尚未接入旧 probe，因此不把 R1 称为诊断优势或 fresh-seed 证据。

诊断臂原 run `R1_DIAG_S11_IC_001` 在经验库并发写入时失败，候选评价已保留；修复文件锁/原子写入后在新 run 以相同提案恢复，不改评价器。两臂的 null、未接线配置与预算挤占已导出为 proposer 专用 trace；见 `agents/runs/R1_CONFIG_IC/result.json` 和各 run 的轻量 `result.json` / `trace.jsonl`。

### R2：连续 research-harness evolution

复用 `run_evo_step.py --proposer llm --prepare` 与 `--edits` 的两阶段接口。`--trace-file` 只接受剔除原始行情和 holdout 结果的结构化轨迹；`--evaluate-only` 给固定 harness 和冻结候选提供同一评价管线；`--dev-split seeded-year-blocks` 以年度分层的连续日期块划分 50% fit / 25% probe / 25% dev-holdout。修复后的预算先预留 crossover，再按 seed 轮转 mutation；`mutation_per_seed`、`duplication_corr` 已接线，容量不足时拒绝重复填充。

本轮 `agent-58` 续用 R1 诊断上下文并实际读取执行 trace，提出 3 条编辑；`crossover_pairs: 2 → 0` 被采纳，另外两条未获支持：crossover=1 的 probe 主差值为 `+0.0000341`，mutation depth=2 为 `−0.0091876`。与固定 harness 相比，seed 11 dev-holdout 平均 |IC| 为 `0.0529996 → 0.0557874`（`+0.0027878`）；冻结 adopted harness 后，预定 seed 29 fresh dev split 为 `0.0300937 → 0.0338255`（`+0.0037318`）。两次 coverage 均为 1.0，turnover 未增加；seed 29 pass_count 为 `10 → 12`。这是实际的研究资源分配改动，不是只生成一个公式，但不构成普遍优势或独立 OOS。

两次 novelty 均由 `0.5` 降为 `0.125`，duplicate_rate 均增加约 `0.01515`。因此不能宣称机制多样性提升，也不能由两个弱 `rank_minus` 候选推断所有 crossover 都无用。原始提案、负结果、固定对照、冻结文件、split 和恢复命令见 `agents/runs/R2_CONFIG_001/result.json` 与各 run；源码恢复点为 `e80a349`。

共同样本修复后的固定配对复核使用 `COMMON_SAMPLE_R2_PAIR_S11_001` 与 `COMMON_SAMPLE_R2_PAIR_S29_001`：seed 11 select 为 `0.0535600 → 0.0563769`（`+0.0028169`），seed 29 为 `0.0303300 → 0.0341058`（`+0.0037758`），方向与修复前一致。两次 select coverage 分别为 `0.876326` 与 `0.926004`，不是旧评价器的 1.0；novelty 仍均由 `0.5 → 0.125`。seed 11 turnover 增加约 `0.000830`，seed 29 下降约 `0.000852`，不再保留“turnover 未增加”的旧表述。配对结果与支持哈希见各 run 的 `result.json`，不覆盖上述历史结果。

完成线包括至少一个诊断驱动的 harness 改动在预定 dev 主指标上优于固定 harness，并在 fresh dev seed 上同向复核；不以框架跑通代替这一结果。遇到门禁或执行设计问题时，在独立版本中修改并重跑，而不是停止研究或改写历史。

### R3：有界互补机制修复（完成，分支停止）

以 `crossover_pairs=0` 为固定基线，在 seed 43 检验符号一致、考虑互补性的双亲组合。两次 `opencode-go/deepseek-v4.1-flash` 上下文均在首个响应前失败且没有 R3 文件读取，故障现场保存在 `R3_TRACE_S43_001`。随后在新 run 改用 primary 模型，实际读取 R2 trace 并提出 4 个原子组合；模型变化不代表同模型连续证据，也没有改变评价协议。

实现限定为 `signed_rank_sum` 与 `diversified`：两个父信号各自用 fit IC 定号后等权相加，配对按 fit 强度与 IC 序列相关性选择。`skills.crossover configure` 原子设置 slots、规则与配对策略。`composition` 在 probe 阶段执行 novelty/duplicate 约束，proposer 不能放宽；旧 `rank_minus` 与默认 compose 行为不变。这里的 novelty 是前 8 个候选与其 seed 父因子在至多 6 个 fit 日的逐日横截面 rank 相关取均值后，`|corr| < 0.7` 的候选—父对比例；不是机制计数或候选间相关，不能外推为经济机制多样性。主指标为 select 平均 |IC| 相对固定基线的差值，阈值 `>0.002`；pass_rate、coverage 不下降，turnover 增幅不超过 0.02，novelty 不下降、duplicate_rate 不增加。

实际执行了 pairs=1/2/4/6 四种组合，probe 平均 |IC| 分别增加 `0.003119`、`0.005321`、`0.008037`、`0.008357`，但 novelty 全部下降，因此全部不采纳；4/6 槽的 turnover 也超过 0.02。duplicate_rate 的改善不能替代预定 novelty 约束。未采纳候选没有进入 select，adopted 仍为 `crossover_pairs=0`，主指标差值为 **0**，seed 71 未运行。结论是**当前预算与双亲池下，没有发现同时满足 IC 与多样性约束的组合，停止该分支**，而不是证明组合普遍无效。未放宽门禁、未换指标、未根据结果继续调参。详见 `agents/runs/R3_CONFIG_002/result.json` 和 `agents/runs/R3_CONFIG_001/result.json`。

### R4：外层路线选择（固定单决策真实对照完成）

R1–R3 暴露的接口缺口是：模型只能提交局部编辑，不能正式选择停止、换路或重设问题；原 reflection 只沉淀有支持的改进，不等于重新审视研究假设。继续调 crossover 不能修复这个控制层缺口。

新增 `skills/current/factor-evolution-harness2/scripts/run_route_step.py` 作为薄外层，不复制 evaluator，也不改旧 RSIF 链。`local-only` 与 `route-choice` 共用同一份中性 trace：保留提案参数、probe 结果和门禁事实，去掉旧 `intent` / `next_question` 等文字结论。外层 prepare 不读取市场面板，模型只看到研究状态、probe trace、route history 与可编辑 surface。

- `EXPLOIT / REPAIR`：把现有 2–5 条局部编辑委托给 `run_evo_step.py`，沿用同一评价管线。
- `FORK / REFRAME`：输出 `next_state.json` 和新研究问题，由新 run 承接；不在本轮更换 base harness 或 evaluator。需要新指标时，必须另设协议并重跑固定基线，不能事后改写旧结果。
- `STOP`：记录外层停止，不执行候选、不伪造 evo step。
- `route_history.json` 保存路线决定及失败，与只记录有支持改进的经验库分开。外层委托禁用经验库写入并检查其索引未变，避免两臂共享可变记忆；旧 runner 默认经验行为不变。无效提案原文、执行失败和 handoff 都保留，部分执行现场不直接复跑；child 日志与 select 结果不进入下一轮 proposer 输入。不恢复 claims/reviewer 审计链。

R4 的真实对照在执行前冻结模型、初始证据、总预算、seed、最大决策次数与主指标，只比较 local-only 与 route-choice 的权限差异。固定 Kimi K3.1、每臂一次决策、一次模型调用和 12 个候选预算的真实 run 已完成：local-only 的 `R4_LIVE_LOCAL_KIMI_S11_004` 因非法算子删除而未执行，route-choice 的 `R4_LIVE_ROUTE_KIMI_S11_002` 选择 `EXPLOIT` 但未采纳编辑，两臂最终均为 base harness。冻结后的共同样本终评 `R4_LIVE_KIMI_PAIR_S11_001` 与 `R4_LIVE_KIMI_PAIR_S29_001` 显示 route 减 local 的 select 平均绝对 IC 增量均为 0，支持哈希一致。该结果不支持单决策路线优势，也不能外推到多步路线自治或独立 real-data OOS；完整记录见 `agents/runs/R4_LIVE_KIMI_RESULT_001/result.json`。

连续控制与评价接口已补齐：

- `run_research_loop.py` 通过外部 proposer 命令衔接 prepare、提案与执行，自动承接 `next_state.json`，分别记录决策数、模型调用、输入/输出 token 和候选预算。proposer 只读 generation 白名单文件，必须写 `model_usage.json`；故障现场可恢复，已有完整提案与用量时不重复调用模型。
- `research_contract.py` 固定在线 `mean_abs_ic` 主增量、coverage/pass_rate/turnover/novelty/duplicate_rate 约束、无改进停止和修复深度。`active_branch` 承接每轮基于 probe 选出的已执行候选，可以继续处于负增量或未采纳状态；`champion_harness` 仅在当前 champion 与候选处于共同支持且满足任务采纳契约时更新。local-only 的 REPAIR 由驱动器确定，route-choice 仍由模型选择路线。
- `compare_harnesses.py` 对两个或更多冻结 harness 使用同一 comparison group，禁止跨 child result 直接比较。`--probe-only` 排除 select 评价，正式终评同时输出 probe/select，但不根据 select 重新选择 harness。
- `gepa_harness_adapter.py` 复用 GEPA `0.1.4` 的 `evaluate`、`batch_evaluate` 和 `make_reflective_dataset`。候选仅含 `proposer_guidance`，通过 `run_evo_step.py --prompt-guidance-file` 进入 prompt 哈希与恢复身份；联合候选在同一共同 probe 样本上计算平均绝对 IC 增量及多目标分数，reflective dataset 不含 select。
- `run_gepa_optimization.py` 固定 train/validation data id 分离、任务与反思模型一致，分别持久化 proposer/reflection 调用与 token 预算，关闭 W&B、MLflow 和评价缓存。两类模型包装器均在每次调用前及调用后检查预算，持久化用量在恢复后继续生效，GEPA 重试不能绕过已耗尽的额度。`proposer_command.py` 与 `BudgetedReflection` 复用现有 OpenAI 兼容模型客户端，只发送白名单证据。当前候选组变化时生成新的 comparison run，保留各次支持口径；最佳 harness 仍需冻结后通过独立共同样本终评。

固定 Kimi K3.1 的 GEPA 真实优化位于 `agents/runs/GEPA_LIVE_OPT_005`：最多 4 次任务提案、1 次反思和 4 次评价的限制全部满足，实际用量分别为 3、1、4 次。验证集的空 guidance 提案包含非法算子删除，保留显式失败惩罚与 `metric_available=false`；训练集候选的 probe 平均绝对 IC 增量为 `+0.0127361`，但 novelty 下降 `0.4`、duplicate_rate 增加 `0.0454545`，未通过采纳契约。反思生成的候选未产生更高分数，最佳候选仍为空 guidance，因此没有 seed 43 确认评价，也没有 GEPA 文本增益结论。汇总见 `agents/runs/GEPA_LIVE_KIMI_RESULT_001/result.json`。

### 核心诊断 continuation（V001）

执行协议见 `docs/DIAGNOSTIC_CONTINUATION_EXECUTION_PLAN_CN.md`，可编辑配置位于 `config/diagnostic_continuation_v1/`。四个任务先通过真实固定 harness 的 probe-only 筛选确认初始缺陷，筛选未执行 select：T2 的 duplicate_rate 为 `0.0909–0.1364`，T3 的 pass_rate 为 `0.8333–0.9167`，T4 的三个 seed 均有负 `year_ic_min`。逐任务与 seed 的初始安全 trace 来自对应筛选结果。

- `run_research_loop.py --fixed-decisions` 关闭无改进与修复深度提前停止，在预定决策耗尽后写只读 `branchpoint.json`。两个 continuation 从同一活动状态和 champion 恢复，分支点身份进入各自状态记录。
- `run_route_step.py --prompt-feedback parent|diagnostic` 控制逐候选 trace。两臂读取相同的 `shared_research_memory.json`，其中历史 harness 编辑、probe 结果、失败状态和适用范围均有来源；当前任务的 select 与嵌套敏感字段在读取时被拒绝。`proposer_command.py` 将该文件纳入实际模型白名单输入。
- `run_evo_step.py --probe-only` 在探索内不执行 select。活动分支按 proposal order 处理并列，从已执行单编辑候选中选择 probe `mean_abs_ic` 最大者；当前 champion 作为额外 arm 进入同一共同支持组，采纳判定直接相对 champion，避免负增量中间状态后的局部恢复覆盖更优 champion。
- `run_diagnostic_continuation.py` 依次执行共享探索、parent-only、diagnostic 和冻结共同比较，检查分支点、split、共享记忆、实际调用次数、内部 select 排除及 champion 文件。最终比较同时纳入 fixed-harness 与共享 champion 辅助参照。
- `diagnostic_results.py` 汇总 12 个配对差值、任务 seed 中位数、留一任务敏感性、成本与失败。日期块诊断明确使用 `mean_daily_abs_ic_delta`，与候选池主指标分开命名。

V001 十二个真实配对单元已全部完成，全部协议检查通过。diagnostic 减 parent 的差值包含五个正值、六个零值和一个负值，总体中位数为 `0.0`。任务 seed 中位数仅 T2 为正，为 `+0.0030611`；T1、T3、T4 均为 `0.0`，四个留一任务中位数也均为 `0.0`，最终证据状态为 `development-panel null`。

辅助比较显示，parent-only 减 fixed-harness 的 select 差值中位数为 `+0.0130691`，十一个单元为正、一个为零；diagnostic 减 fixed 的中位数为 `+0.0158895`，十二个单元均为正，最小值为 `+0.0068560`。模型驱动的共享探索与 continuation 在该开发面板中普遍超过 fixed-harness，核心诊断信息包没有形成预定的稳定额外增量。这里没有 untouched 独立 OOS。

面板实际完成 `72/72` 次 Kimi 调用，输入 `1,051,893` tokens，输出 `307,483/576,000` tokens。两条输出超限失败均保留；T3/seed 43 在新恢复目录继承第一次调用的已耗预算并完成第二次决策，单元总调用与 token 均未突破冻结上限。`run_research_loop.py` 对固定协议中的单次输出失败记录失败决策并在剩余额度内继续，未开始执行的候选不消耗候选位置。`report_diagnostic_panel.py` 另行计算实际执行成本：原始候选预算记账 `852/864`，真实子执行候选位置 `840`。

V001 后半面板采用任务与 seed 单元级受控并行，同一输出目录不并发，单元内部仍保持共享探索、两个单链 continuation 和冻结共同比较的固定顺序。并行调度不改变模型、预算、种子、评价器、champion 规则或十二单元证据口径。完整结果位于 `agents/runs/DIAGNOSTIC_CONTINUATION_RESULT_V001/result.json`，执行摘要位于 `DIAGNOSTIC_CONTINUATION_RESULT_V001_EXECUTION_SUMMARY_001/result.json`。

### 累计记忆组件消融（V002）

执行协议见 `docs/MEMORY_COMPONENT_ABLATION_EXECUTION_PLAN_CN.md`，配置位于 `config/memory_component_ablation_v002/`。V001 表明完整研究流程相对 fixed-harness 有开发面板增益，诊断信息包的额外增量缺乏一致性；V002 因此将累计记忆从第一次决策开始独立消融，并改用 T5–T8 新任务批次。

- fixed-harness 不调用模型；scalar-only 使用空记忆和标量反馈；scalar+memory 添加真实历史 harness、编辑与 probe 记录；scalar+memory+diagnostic 再添加逐候选诊断。三个模型臂独立执行两次决策，不使用共享探索分支点。
- 主要估计量为十二个 memory-minus-scalar select `mean_abs_ic` 差值的中位数。fixed 与 diagnostic 比较作为辅助结果，支持条件仍要求总体中位数、至少三个任务中位数和全部留一任务中位数为正。
- `run_memory_component_ablation.py` 检查初始 harness 身份、空记忆与共享记忆、诊断输入、实际调用次数和内部 select 排除。真实历史记忆不含当前实验 select 或原始市场行。
- T5–T8 已完成真实 probe-only 筛选。T7 使用六个 crossover 槽位，避免与 T5 的八槽位配置生成相同 roster；T8 引入 `momentum_60` 与 `volatility_60`。筛选结果与逐 seed 初始 trace 均已版本化保存。

V002 十二个真实配对单元已全部完成，全部协议检查通过。memory 减 scalar 的差值包含六个正值、一个零值和五个负值，总体中位数为 `+0.0012239560`。任务 seed 中位数分别为 T5 `+0.0062649974`、T6 `+0.0101487023`、T7 `−0.0011484275`、T8 `−0.0009005130`，只有两个任务为正。排除 T5、T6、T7、T8 后的中位数分别为 `0.0`、`−0.0009005130`、`+0.0024479120`、`+0.0054547897`，最终证据状态为 `development-panel null`。

辅助比较中，memory 减 fixed 的中位数为 `+0.0094226906`，scalar 减 fixed 为 `+0.0104107660`，diagnostic 减 memory 为 `0.0`。累计记忆没有超过 scalar-only 的预定一致性标准，诊断组件在累计记忆之上也没有稳定增量。面板实际完成 `72/72` 次有效 Kimi 调用，输入 `1,309,232` tokens，输出 `277,654/576,000` tokens，真实执行候选位置为 `732`。两次错误模型通道尝试均被身份检查拒绝并单独保留，`13` 条失败或无效决策记录未删除。完整结果位于 `agents/runs/MEMORY_COMPONENT_RESULT_V002/result.json`，执行摘要位于 `MEMORY_COMPONENT_RESULT_V002_EXECUTION_SUMMARY_001/result.json`。

### 论文实验准备（V003）

目标与执行协议见 `docs/PAPER_READINESS_V003_EXECUTION_PLAN_CN.md`，配置位于 `config/paper_readiness_v003/`。V001、V002 保留为探索性面板，V003 使用 T9–T14 新任务、两个开发划分、两次独立模型复现和每臂四次决策。

- `AUTO-RESEARCH-ATOMIC-PROPOSAL-1` 将 2–5 个编辑组成一个最终 harness，避免模型组合意图被拆成独立候选。旧 JSON 数组继续表示独立单编辑候选。
- `random_proposer_command.py` 在相同可编辑表面上生成合法原子提案，模型调用计数为零。scalar、memory、diagnostic、random 与 fixed 使用相同候选评价和冻结共同比较。
- `run_paper_readiness_v003.py` 执行 24 个配对单元，分别记录开发划分和模型复现。基础设施恢复重放已持久化提案，不新增模型调用。
- 主要对比为 memory 减 scalar、diagnostic 减 memory、memory 减 random 和 memory 减 fixed。汇总包含任务聚类 bootstrap 区间、精确任务符号翻转检验和组件对比的 Holm 校正。
- 24 个配对单元和 288 次有效 Kimi 调用已全部完成，全部协议检查通过。完整结果与派生证据已纳入论文工作区的 `evidence/` 目录。

V003 中，memory 减 random 的中位数为 `+0.0035336384`，二十个单元为正、四个为负，六个任务中位数均为正，任务聚类 95% 区间为 `+0.0016230605..+0.0051187463`。memory 减 fixed 的中位数为 `+0.0109768722`，二十四个单元均为正，区间为 `+0.0080797934..+0.0154555391`。模型驱动流程在该开发面板中超过随机合法编辑搜索和固定初始 harness。

memory 减 scalar 的中位数为 `+0.0002895444`，十二个单元为正、四个为零、八个为负，Holm 校正 `p=1.0`；diagnostic 减 memory 的中位数为 `0.0`，四个为正、十七个为零、三个为负，Holm 校正 `p=1.0`。累计记忆与诊断组件的独立增量未通过冻结组件检验。memory 减 random 的 novelty 中位数同时下降 `0.0916666667`，duplicate_rate 增加 `0.0378787879`，论文需要同时报告行为代价。

面板输入 `7,550,828` tokens，输出 `1,200,323/2,304,000` tokens，模型臂真实候选位置 `3060/3456`，随机臂 `1104/1152`。一次无用量超时通过相同 PREPARED 输入恢复，没有新增计费调用。论文表格与复现诊断位于 `writing_runs/auto_research_harness_v003_20261031/evidence/`。

验证为 `tests/test_research_loop.py`、`tests/test_route_choice.py`、`tests/test_research_harness_evolution.py`、`tests/test_diagnostic_continuation.py`、`tests/test_memory_component_ablation.py`、`tests/test_paper_readiness_v003.py`、`tests/test_proposer_command.py`、`tests/test_gepa_harness_adapter.py`、`tests/test_research_governance.py`、`tests/test_local_model_runtime.py`、`tests/test_local_quote_library.py`、`tests/test_price_volume_baselines.py`、`tests/test_price_volume_batch.py`、`tests/test_mentor_clean.py` 和 `tests/test_data_corrections.py`，共 141 项。真实 dev 工程复核的历史结果保持原有记录，不构成新的研究效果证据。

## 7. 轻量证据与默认检查

- 探索 run 默认只留 `config`、`result`、必要 `trace` 和恢复命令。失败、null、无效提案同样保留，不覆盖旧 run。
- 探索不需要逐轮 claim、snapshot、reviewer、长篇报告或 hash 证明链。确认性对照再按问题固定 seeds、预算和评价口径。
- 默认运行相关测试与真实小调用；`benchmark/ci.sh` 只是现有快速回归，不是研究有效性的证明。
- `ci/guard.sh` 保留给旧 claims/论文流程；默认 pre-commit 不再执行该完整链。
- 论文、投稿和历史 claim 回填不是当前主线。没有独立数据时，不把 dev-holdout 写成 OOS。

## 8. 数据与比较边界

- 市场数据只读本地 `Data/` 与 `data_processed/`；禁止外部市场取数。模型推理论与文献检索不等于市场数据取数，但不得发送原始行情或封窗内容。
- dev 为 2017-01-01..2020-11-30；embargo 与已读 score/OOS 窗口不重读。2023+ non-independent research 不是 final OOS。
- 探索中的 coverage 只记录和诊断，不作全局 STOP；不得静默填补或删周，跨候选比较使用共同可评估样本。共同支持是相关 roster 与父因子的 `date+symbol` 交集，先在交集内重新排名再计算 IC/turnover/novelty；最终主指标使用 `adopted_reference`，初始提案 delta 保留其 comparison group reference。
- 正式 v2 生产准入、回测和其覆盖率停止规则仍按根目录 `docs/V2_RULES.md`，不与探索流程混用。

## 9. 当前证据与文献定位

- 当前 seed11 真实 QD：QD 0.131398、random 0.131761、random-mutation 0.088618；top-5 含 3 个 library。它是描述性单 seed 结果，不支持普遍优势或整体无效。见 `agents/runs/DEEPSEEK_QD_S11_COVLOG_001/completion.json`。
- [Self-Evolving Harness](https://arxiv.org/html/2609.38372v1)：同一模型读取轨迹并修改执行 harness；同时提示单链与宽松采纳的局限。
- [OO-Harness](https://arxiv.org/html/2610.28380v1)：可继承、可路由的 harness 类层级，而非单一全局 harness。
- [Beneath the Diff](https://arxiv.org/html/2609.00077v1)：代码差异不等于算法机制多样性。
- [ConflictGuide](https://arxiv.org/html/2609.39933)：诊断反馈有价值，但引入时机也会改变搜索收缩。
- [Kernel Autoresearch](https://arxiv.org/abs/2610.10394)：固定 grammar 的表达限制与可执行程序/构造契约。
- [AI Research Teams](https://arxiv.org/abs/2610.28887)、[Fork-and-Flush](https://arxiv.org/abs/2610.07447)：研究记忆、预算分配、分支与 idea basins。

这些是设计参考，主要是 2026 年预印本且不含金融因子证据；不把其效果数字外推到本项目。

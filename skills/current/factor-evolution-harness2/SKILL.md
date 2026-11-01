---
name: factor-evolution-harness2
description: Run bounded factor-harness evolution with local Propose–Probe–Compose and an optional outer route step (EXPLOIT/REPAIR/FORK/REFRAME/STOP) using sanitized probe traces. Use when iterating on how factors are mined or reconsidering the research route; development data only, never load the score window.
disable-model-invocation: true
---

# Factor Evolution Harness²（v2 evo loop）

本 skill 是**因子进化流程的进化器**，不是再实现一遍 `factor-pool-evolution`（那个是单轮 mutation/crossover 建议器）。
借鉴 Google Research **Harness²**（[repo](https://github.com/google-research/harness2)、[arXiv:2610.14224](https://arxiv.org/abs/2610.14224)）的两级递归与 contrastive Propose–Probe–Compose，但落成完全本地、确定性的实现。

## 核心概念

- **execution harness** = 本次进化的对象：算子库（operators）、seed 池策略、crossover 规则、probe 指标、预算与停止规则。
- **task-level recursion（本 step）**：先跑 reference（当前 harness 的 matched probe）→ 提 INV/DOM 两类候选编辑 → 每个候选在同一开发窗、同预算同 seed 下 probe 一次 → **按行为对比选择性 compose**（kept/revised/dropped，每个决定带数字证据）→ 可选 k 个 composition 盲选。
- **harnessing-level recursion（反思）**：把本轮有执行对比支持的结论写成一条 `EXPERIENCE.md` 条目（cue / lesson / evidence / confidence），按 domain 存库；下一 step 的 Propose 选择性复用，**不直接继承**。
- **硬边界**：只读开发窗 `2017-01-01..2020-11-30`；`2021-01-01..2023-12-31` 评分窗从不加载；失败/未知不记 0。生产默认低覆盖或超时停止；auto-research 显式 `--coverage-log-only` 时覆盖率只记录诊断，不触发全局 STOP。

## 何时使用

- 想改"我们怎么挖因子"（加/减算子、改 probe、改 crossover、改预算）时；
- 想比较两个 harness 在固定预算下的行为差异（通过数、平均|IC|、覆盖率、重复率、新颖性）；
- 想把一轮改进结论沉淀成可迁移经验，下一轮自动带入。

不要用于：单因子打分/回测/准入（请用 `docs/V2_RULES.md` 的链路）；从零生成因子表达式（请用 `factor-idea-generation` / `local-factor-discovery`）。

## 用法

```bash
# 合成数据快速自检（无 IO、无真实数据）
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_evo_step.py \
  --demo --k 1 --output evo_rsif/runs/evo_loop_v2_demo_synthetic

# 真实开发窗（本地面板，只读 2017-01-01..2020-11-30；首次会缓存到 run 目录）
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_evo_step.py \
  --run-id evo_loop_v2_step2_20261020 --k 3 --mode parallel --budget 24 \
  --use-lessons

# M2：LLM 提议器两阶段（脚本只负责 prompt pack / 校验 / probe / compose）
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_evo_step.py \
  --run-id evo_loop_v2_llm --proposer llm --prepare --k 2 --budget 24 --use-lessons
# 模型读 generation/prompt.txt，把候选编辑写到 generation/edits.json，然后：
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_evo_step.py \
  --run-id evo_loop_v2_llm --proposer llm --edits evo_rsif/runs/evo_loop_v2_llm/generation/edits.json
```

产物（append-only，新 run 新目录）：

```
<run>/
├── run_manifest.json          # 输入/哈希/守卫/状态
├── harness/base.json          # 本轮 base harness
├── harness/adopted.json       # 本轮采纳的 harness
├── probe/reference.json       # reference probe（probe / select 两个 split）
├── probe/candidates/*.json    # 每个编辑的 matched probe
├── probe/adopted.json         # 采纳 harness 的 probe
├── compose/decisions.json     # kept / dropped + 每个决定的数字证据
├── step_record.json           # 完整 step 记录（可复现）
├── ledger.jsonl               # append-only 事件流（含 STOP / 失败）
└── REPORT_CN.md               # 人读报告
```

经验库：`evo_rsif/experience/<domain>/EXPERIENCE.md` + `index.json`。

## 与 v1 的区别（来自 Harness² 的借鉴）

| Harness² 机制 | 本实现 |
|---|---|
| 两级递归 | step 级精修 + 每 step 反思入经验库，下轮由 `--use-lessons` 选择性读取 |
| Propose–Probe–Compose | 确定性编辑器目录（INV/DOM）→ 同窗同预算 matched probe → 带数字证据的 compose |
| 无 ground-truth 的对比 | 不用单一分数排名；比较 pass_count / mean|IC| / coverage / dup / novelty / year stability 的行为对比 |
| k 预算 + parallel/sequential | `--k` + `--mode parallel|sequential`；k>1 用匿名 select-split 盲选 |
| 失败不入零 / 库隔离 | ledger 记录 STOP/失败；每个 run 独立目录，score window 不加载 |

## M2 状态

- **固定评估预算**：先预留 crossover，再按 seed 轮转 mutation、raw seeds 补位；`mutation_per_seed` 与 `crossover_pairs` 实际接线。预算超过唯一候选容量时明确失败，不用重复项凑数。
- **共同可评估样本**：每个 comparison group 先生成全部 roster，再取所有候选、标签和 research mask 的 `date+symbol` 交集；种子方向、配对强度、IC、evaluation turnover 与 novelty 均在对应的共同 fit/eval 支持上计算并重新排名。记录 `common_cells/common_dates/common_support_sha256` 和 fit 对应诊断；最终 adopted/reference 使用同一支持，轻量主指标读取 `adopted_reference`，各提案 delta 保留其初始 comparison group reference。共同支持为空时指标为空且 coverage 为 0，不静默填补。
- **硬探针**：top-quartile |IC|、ICIR p25、turnover 代理（top-fraction 换手）、年度稳定性、novelty、duplicate rate；composition 支持规则为 pass_rate → mean|IC| → ICIR p25 → top|IC|（turnover 守卫）。年度与重复率阈值是诊断，不会自行剪枝。
- **LLM 提议器（两阶段）**：`--proposer llm --prepare` 生成中性模板与 prompt pack；`--prompt-feedback parent|diagnostic` 控制反馈，`--trace-file` 可加入剔除 holdout/原始行情的真实执行轨迹。trace 顶层、proposal、edit 与 candidate roster 使用共享白名单，并在所有嵌套层级拒绝 holdout/select/child 结果别名；`child_run` 仅作为谱系引用保留。模型回写 2–5 条 `edits.json` 后，以同一参数和 `--edits` 恢复；脚本在任何目录创建、ledger/cache/artifact 写入和 probe 之前只读校验身份，非法编辑或身份不匹配均 fail-closed，失败恢复不改变 PREPARED 现场字节。
- **固定对照与 fresh dev split**：`--evaluate-only` 用同一管线评价未修改 harness；加 `--compare-harness <frozen.json>` 时，base 与 frozen alternate 在同一 probe/select comparison group 中评价，不能跨两个独立 run 直接比较各自交集。`--dev-split seeded-year-blocks --seed <n>` 将日期分为 50% fit / 25% probe / 25% dev-holdout，fit 与评价分离。冻结候选后以 `--evaluate-only` 和新 seed 复核；这不是独立 OOS，k>1 的组合盲选也不等于独立重复。
- **有界组合与多样性门禁**：`skills.crossover` 以 `configure` 原子设置 pairs/rule/可选 pair_selection，避免从 0 开启规则却不产生候选。`signed_rank_sum` 使用两个父信号各自的 fit IC 定号后等权相加；`diversified` 按 fit 强度 × (1 − |fit IC 序列相关|) 选配对。`composition.min_delta_novelty` 与 `max_delta_duplicate_rate` 可在 probe compose 阶段拒绝多样性损失，默认不启用新门禁。`--research-question` 将研究问题纳入 prompt 与恢复身份。
- **轻量结果**：`auto_research/scripts/summarize_research_step.py <run>` 生成 `result.json` 和 `trace.jsonl`；加 `--export-trace` 另行生成无 holdout 结果的 proposer trace。若 run 已有输入 `generation/execution_trace.json`，用 `--export-trace --export-only` 验证已有结果并写 `generation/next_execution_trace.json`，不覆盖输入。经验库并发写入使用文件锁与原子替换。
- 仍未做：经验库迁移实验（跨家族/新 domain）、接入生产真实挖掘链路的默认开关。

## 外层 route step（固定单决策对照无优势，多步自治未验证）

`scripts/run_route_step.py` 在单步 evo loop 外提供有界路线选择，不替代其 evaluator。`local-only` 只允许原有 2–5 条编辑；`route-choice` 额外允许 `EXPLOIT / REPAIR / FORK / REFRAME / STOP`。两臂共享同一份去除旧文字结论的 probe trace，可用于匹配对照。

- `EXPLOIT / REPAIR`：委托 `run_evo_step.py` 执行局部编辑，保留原有候选评价、compose 和结果契约。
- `FORK / REFRAME`：只写新问题的 `next_state.json`，由新 run 承接；不在本轮改变 base 或 evaluator。新指标必须另设协议并重跑基线。
- `STOP`：只写外层停止结果，不伪造 evo step。
- 外层 prepare 只读 harness 与安全的结构化 trace，不读取市场面板。`route_history.json` 保存路线与失败，模型读取的是 `generation/route_history_input.json` 快照。child 通过 `--disable-reflection-write` 禁止经验库写入，旧 runner 默认行为不变，并核对经验索引未变化、`lessons_used=[]`，避免两臂共享可变记忆。下一轮看不到 select/holdout 结果；child 日志和 `child/step/result.json` 不得进入 proposer 输入。`trace.search_policy` 是上一轮基线，实际父 harness 以 `research_state.current_harness` 为准。模型调用由外部 agent 完成，本脚本不选择模型、不发起网络请求。

```bash
# 准备新 run；local-only 对照将 --mode 改为 local-only
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_route_step.py \
  --output auto_research/agents/runs/<new-route-run> --mode route-choice \
  --harness auto_research/agents/runs/<config>/base_harness.json \
  --trace-file auto_research/agents/runs/<previous-run>/generation/next_execution_trace.json \
  --research-question "<bounded question>" --model-id <fixed-model> \
  --seed <n> --dev-split seeded-year-blocks --budget 12 --coverage-log-only --prepare

# route-choice：模型只读 generation 中指定文件，写 generation/route.json。
# 保持其余参数不变，去掉 --prepare，加入：
  --route auto_research/agents/runs/<new-route-run>/generation/route.json
# local-only 对应写 generation/edits.json，并以 --edits <path> 恢复。
```

外层保留 `run_manifest.json`、`result.json`、`route_history.json`、必要 prompt/trace 和可选 `next_state.json`。实际局部执行位于 `child/step/`，其 `next_execution_trace.json` 只含 probe 证据。`--demo` 可做合成端到端验证；测试为 `tests/test_route_choice.py`，不证明路线自治有效或提供独立 OOS。

固定 Kimi K3.1 的真实单决策对照见 `auto_research/agents/runs/R4_LIVE_KIMI_RESULT_001/result.json`：local-only 提案无效，route-choice 未采纳编辑，两臂在 seed 11/29 的共同样本 select 主指标差值均为 0。该对照只覆盖每臂一次决策和一次模型调用。

## 连续研究 loop 与共同样本终评

`research_contract.py` 使用 `AUTO-RESEARCH-LOOP-CONTRACT-1`：预算包含最大决策数、候选预算、模型调用与 token、无改进次数和修复深度；在线主指标固定为 probe 平均绝对 IC 增量，coverage、pass_rate、turnover、novelty 和 duplicate_rate 约束全部显式给出。终评使用 select，在线 proposer 不读取 select。

`run_research_loop.py` 自动执行 prepare → 外部 proposer → route step → next state。proposer 是可执行文件，通过环境变量接收 `AUTO_RESEARCH_GENERATION`、`AUTO_RESEARCH_PROPOSAL`、`AUTO_RESEARCH_USAGE`、`AUTO_RESEARCH_MODE`、`AUTO_RESEARCH_MODEL_ID` 和 run id。它只能读取 generation 中指定的安全文件，并写 `model_usage.json`：

`scripts/proposer_command.py` 已接入现有 `auto_research/agents/llm.py`，可直接作为该可执行文件。模型通道沿用 `AUTO_RESEARCH_LLM_PROVIDER`、`AUTO_RESEARCH_LLM_BASE`、`AUTO_RESEARCH_LLM_MODEL` 和相应凭据环境变量；`AUTO_RESEARCH_MODEL_ID` 必须与配置模型一致或以其 provider 全名结尾。命令只发送白名单 prompt/JSON，拒绝符号链接与嵌套敏感字段，缺少真实 token usage 时终止。`AUTO_RESEARCH_PROPOSER_MAX_TOKENS` 控制单次输出预算。

`scripts/claude_proposer_command.py` 提供相同协议的 Claude CLI 后端，默认使用当前 CLI 模型，只有显式设置 `AUTO_RESEARCH_CLAUDE_CLI_MODEL` 才覆盖模型。返回的 `modelUsage` 必须与 `AUTO_RESEARCH_MODEL_ID` 一致；工具全部禁用，会话不持久化，局部编辑使用对象包装 schema。单次输出超限会把实际 usage 与 `per_call_budget_exceeded.json` 一并保存，连续驱动器在候选执行前停止并完整记账。Kimi K3.1 对应标识为 `kimi/k3.1-pro[1m]`。

```json
{
  "schema": "AUTO-RESEARCH-MODEL-USAGE-1",
  "model_id": "<fixed-model>",
  "calls": 1,
  "input_tokens": 0,
  "output_tokens": 0
}
```

token 必须填实际用量，并在模型响应解析前持久化，结构化输出解析失败也计入预算。驱动器在候选评价前检查预算；`active_branch` 承接每轮基于 probe 选出的已执行候选，可以继续处于未采纳状态，`champion_harness` 仅在共同支持上满足任务契约时更新。local-only 的 `--local-action REPAIR --repair-target <target>` 由驱动器传入，既有手工 local-only 调用默认仍为 EXPLOIT。

```bash
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_research_loop.py \
  --output auto_research/agents/runs/<new-loop> \
  --contract <loop-contract.json> --proposer <executable-proposer> \
  --harness <base-harness.json> --trace-file <safe-trace.json> \
  --mode route-choice --research-question "<bounded question>" \
  --model-id <fixed-model> --seed 11 --budget 12 \
  --dev-split seeded-year-blocks --coverage-log-only

# 从 PREPARE、proposer 或执行现场恢复；可执行文件和契约哈希必须不变。
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_research_loop.py \
  --output auto_research/agents/runs/<existing-loop> --resume
```

`compare_harnesses.py` 将两个或更多冻结 harness 放入同一 comparison group。所有 arm 的开发窗、预算和 split 必须一致；probe/select 支持哈希不一致会失败。它只评价和检查契约，不用 select 重新选择 harness。

```bash
.venv/bin/python skills/current/factor-evolution-harness2/scripts/compare_harnesses.py \
  --arm local=<frozen-local.json> --arm routed=<frozen-routed.json> \
  --contract <loop-contract.json> --output auto_research/agents/runs/<new-comparison> \
  --seed 11 --dev-split seeded-year-blocks --coverage-log-only
# 在线 GEPA 评价加 --probe-only；该模式不写 select 结果。
```

## 共享分支点与诊断 continuation

核心 V001 协议位于 `auto_research/config/diagnostic_continuation_v1/protocol.json`，任务、初始 harness、逐 seed 安全 trace、共享记忆和预算契约均可独立编辑。`--fixed-decisions` 关闭无改进与修复深度提前停止，预定决策完成后生成只读 `branchpoint.json`。continuation 用 `--branchpoint <path>` 恢复，活动状态与 champion 可以不同。

`--prompt-feedback parent` 只提供标量结果、当前谱系与 kept edits；`diagnostic` 额外提供逐候选提案与 probe 明细。两臂读取相同的 `shared_research_memory.json`，该文件保存带来源的历史编辑、probe 结果、失败状态与适用范围，select 与原始市场字段会被拒绝。外层委托始终使用 `run_evo_step.py --probe-only`，因此 select 只在冻结共同比较中执行。

```bash
# 执行一个任务与 seed；已有现场会按各 loop 的恢复协议承接。
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_diagnostic_continuation.py \
  --protocol auto_research/config/diagnostic_continuation_v1/protocol.json \
  --task T1 --seed 11

# 执行完整四任务、三 seed 面板并生成十二单元汇总。
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_diagnostic_continuation.py \
  --protocol auto_research/config/diagnostic_continuation_v1/protocol.json
```

每个单元保存共享、parent、diagnostic 三个 loop 的用量与谱系，最终比较包含 fixed-harness 和共享 champion 辅助参照。`diagnostic_results.py` 计算配对中位数、任务 seed 中位数、留一任务敏感性和日期块 `mean_daily_abs_ic_delta`。共同支持、split、分支点、共享记忆或实际调用次数不一致时标记 `protocol-invalid`。

固定协议中的单次输出超限会记录失败决策和真实用量，并在剩余预算内继续预定决策。旧式提前终止现场可通过 `*_BUDGET_RECOVERY_001` 新目录继承累计用量，只执行剩余决策；原 run 不改写。未开始候选执行的模型失败不消耗候选位置。`auto_research/scripts/report_diagnostic_panel.py` 从真实子执行记录补充实际候选位置与执行成本。

V001 十二单元真实面板已完成，结果为 `development-panel null`：配对中位数 `0.0`，只有 T2 的任务 seed 中位数为正，四个留一任务中位数均为 `0.0`。parent-only 与 diagnostic 相对 fixed-harness 的辅助中位数分别为 `+0.0130691` 和 `+0.0158895`，证据限于开发面板。结果见 `auto_research/agents/runs/DIAGNOSTIC_CONTINUATION_RESULT_V001/result.json` 与 `DIAGNOSTIC_CONTINUATION_RESULT_V001_EXECUTION_SUMMARY_001/result.json`。

## 累计记忆组件消融

V002 协议位于 `auto_research/config/memory_component_ablation_v002/protocol.json`，执行说明见 `auto_research/docs/MEMORY_COMPONENT_ABLATION_EXECUTION_PLAN_CN.md`。三个模型臂从同一初始 harness 独立执行两次决策：scalar-only 使用空记忆，scalar+memory 使用结构化历史成果，scalar+memory+diagnostic 额外读取逐候选 trace。主要估计量为十二个 memory-minus-scalar select 差值的中位数，fixed-harness 进入冻结共同比较。

```bash
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_memory_component_ablation.py \
  --protocol auto_research/config/memory_component_ablation_v002/protocol.json \
  --task T5 --seed 11

# 十二单元齐全后只读汇总。
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_memory_component_ablation.py \
  --protocol auto_research/config/memory_component_ablation_v002/protocol.json \
  --aggregate-only
```

单元检查初始身份、空记忆与共享记忆、诊断反馈、共同支持、实际调用次数和内部 select 排除。任务与 seed 可受控并行，同一输出目录不并发。准备阶段可从内容一致的部分文件与零字节文件恢复，非空冲突文件会被拒绝；不完整公共比较保留原目录，并在新的 `common_comparison_RECOVERY_*` 目录执行。回归位于 `tests/test_memory_component_ablation.py`。

V002 十二单元真实面板已完成，72 次有效 Kimi 调用，全部协议检查通过，最终为 `development-panel null`。memory 减 scalar 的中位数为 `+0.0012239560`，只有 T5、T6 的任务 seed 中位数为正，排除 T5 与 T6 后的中位数分别为 `0.0` 和 `−0.0009005130`。memory 与 scalar 相对 fixed-harness 的辅助中位数分别为 `+0.0094226906` 和 `+0.0104107660`，diagnostic 减 memory 的中位数为 `0.0`。结果见 `auto_research/agents/runs/MEMORY_COMPONENT_RESULT_V002/result.json` 与 `MEMORY_COMPONENT_RESULT_V002_EXECUTION_SUMMARY_001/result.json`。

## 论文实验准备面板

V003 协议位于 `auto_research/config/paper_readiness_v003/protocol.json`，执行目标见 `auto_research/docs/PAPER_READINESS_V003_EXECUTION_PLAN_CN.md`。模型提案使用 `AUTO-RESEARCH-ATOMIC-PROPOSAL-1`，整个编辑数组生成一个最终 harness；旧数组格式保持独立单编辑语义。

```bash
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_paper_readiness_v003.py \
  --protocol auto_research/config/paper_readiness_v003/protocol.json \
  --task T9 --seed 11 --replicate A

# 跳过已完成单元，最多并行两个新单元。
.venv/bin/python auto_research/scripts/run_paper_readiness_panel.py \
  --protocol auto_research/config/paper_readiness_v003/protocol.json

# 24 单元齐全后只读汇总。
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_paper_readiness_v003.py \
  --protocol auto_research/config/paper_readiness_v003/protocol.json \
  --aggregate-only
```

scalar、memory、diagnostic 使用 Kimi，random 使用相同可编辑表面的合法随机原子提案且零模型调用，fixed 不搜索。主要分析保留固定调用预算下的模型失败；基础设施恢复只重放持久化提案。汇总输出任务聚类区间、精确任务符号翻转检验与 Holm 校正。

V003 二十四单元真实面板已完成，288 次有效 Kimi 调用，全部协议检查通过。memory 减 random 的中位数为 `+0.0035336384`，六个任务中位数均为正；memory 减 fixed 为 `+0.0109768722`，二十四单元均为正。memory 减 scalar 与 diagnostic 减 memory 的 Holm 校正均为 `p=1.0`，组件独立增量未通过冻结检验。结果位于 Alba 盘的 `PAPER_READINESS_RESULT_V003/result.json`，论文 CSV 位于 `PAPER_READINESS_RESULT_V003_PAPER_MATERIALS_002/`。

## GEPA proposer 文本进化

安装固定核心依赖：`.venv/bin/python -m pip install -r skills/current/factor-evolution-harness2/requirements-gepa.txt`。

`run_evo_step.py --prompt-guidance-file <text>` 将候选 guidance 加入 prompt，内容、路径和哈希全部进入 PREPARED 恢复身份。`gepa_harness_adapter.py` 的候选只有 `proposer_guidance` 一个文本组件：单个候选使用基线配对增量，batch 中多个候选使用同一共同 probe 样本评分；reflective dataset 只含安全提案、聚合 probe 指标和模型用量。

`run_gepa_optimization.py` 的配置 schema 为 `AUTO-RESEARCH-GEPA-CONFIG-1`，完整可执行示例见 `tests/test_gepa_harness_adapter.py`。trainset 与 valset 的 data id 必须分离，每项数据指定 harness、trace、contract、seed、budget、split、features 和 demo/seed_panel。`proposer_backend` 可选 `openai-compatible` 或 `claude-cli`，任务 proposer 与 `--reflection-lm` 必须使用同一个 model id。`proposer_budget` 限制任务提案模型，`reflection_budget` 独立限制反思模型，`max_metric_calls` 限制 GEPA 评价；两类 token 用量分别持久化。OpenAI 兼容反思使用 `proposer_command.BudgetedReflection`，Claude CLI 反思使用 `claude_proposer_command.ClaudeCLIReflection`，`AUTO_RESEARCH_REFLECTION_MAX_TOKENS` 控制前者的单次输出，后者由单次 CLI 上限与预算共同约束。两种反思后端均在模型调用前及调用后检查预算，恢复后的持久化用量继续生效，后续重试不会增加已耗尽预算的调用计数。

```bash
.venv/bin/python skills/current/factor-evolution-harness2/scripts/run_gepa_optimization.py \
  --config <gepa-config.json> --output auto_research/agents/runs/<new-gepa-run> \
  --reflection-lm <fixed-model>
```

无效 GEPA 提案会保存原文、错误与 `metric_available=false`，使用显式失败惩罚分参与后续反思，不把缺失市场指标记为 0。优化完成后，`confirm_gepa_guidance.py` 在 fresh data instance 上分别生成 seed/best guidance 的冻结 harness，并与原始基线共同评价：

```bash
.venv/bin/python skills/current/factor-evolution-harness2/scripts/confirm_gepa_guidance.py \
  --gepa-run auto_research/agents/runs/<completed-gepa-run> \
  --data <fresh-confirmation-data.json> \
  --output auto_research/agents/runs/<new-gepa-confirmation>
```

W&B、MLflow 和评价缓存关闭；候选组变化时写新的 comparison run。固定 Kimi K3.1 的真实 run `auto_research/agents/runs/GEPA_LIVE_OPT_005` 满足全部调用与 token 预算，最佳候选仍为空 guidance，未采纳文本变更；验证集无效提案和训练集门禁拒绝均已保留。没有变更候选时不执行 fresh-data 确认。结果汇总见 `auto_research/agents/runs/GEPA_LIVE_KIMI_RESULT_001/result.json`。GEPA 输出的变更 guidance/harness 仍需冻结后通过共同样本终评，独立 OOS 当前无证据。

相关回归：`tests/test_research_loop.py`、`tests/test_route_choice.py`、`tests/test_research_harness_evolution.py`、`tests/test_diagnostic_continuation.py`、`tests/test_proposer_command.py`、`tests/test_gepa_harness_adapter.py`。

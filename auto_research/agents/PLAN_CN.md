# Agentic Auto-Research 系统（AI Scientist / Co-Scientist 级）设计

目标：在本地数据与**本地 LLM**（LM Studio，cajal-9b / gemma-26B）上，构建一个端到端 agentic research loop：
hypothesis → critic → 实验（真实 dev 验证器）→ 分析/反思 → tournament/Elo 排序 → 进化 → reviewer 门控。

## 架构（对照 Co-Scientist / AI Scientist / ARIS 的 7 层）

| 层 | 本系统实现 |
|---|---|
| 0 目标接口 | 人类给 research brief（dev 窗因子挖掘），系统自定 hypothesis |
| 1 编排 | Python orchestrator（轻量 DAG/队列）；roles: Proposer / Critic / Experimenter / Analyst / Reviewer / Ranker |
| 2 知识 | KG（claims/experiments/lessons/operators/papers/code）typed retrieval（similar/contradictory/cross_domain/counterintuitive）+ 历史失败台账 |
| 3 假设 | 本地 LLM 生成因子表达式（受限 grammar）+ 经济机制 + 预期指标；Critic 过滤 |
| 4 实验 | 真实 dev 窗评估（复用 expr_search 的 IC/ICIR/turnover/coverage），search/holdout 分离 |
| 5 验证 | claim ledger + reviewer 规则 + holdout；不读 score 窗；不写主库 |
| 6 学习/进化 | Elo tournament + 失败反馈 → 下一代 proposer 提示；可对 harness 提改进建议（reviewer 批准后才采纳） |
| 7 治理 | 本地数据、预算/成本记录、失败模式日志（stagnation/metric fixation）、偏差披露 |

## 与 AI Scientist / Co-Scientist 的差距（如实）

- 无全 PDF 文献库/代码执行 sandbox；知识层只有项目内 KG + claims；
- 无自动论文生成；不做湿实验；
- 规模：本地 9B/26B 模型、单领域（A 股 dev 窗因子），远小于 Co-Scientist 的算力与任务广度；
- 但有真实 verifier + 审计链，可作为"经济、可审计"的缩小版系统。

## 实验设计（系统级 A/B）

- 任务：dev 窗表达式/组合因子搜索；search 70% dev 周选候选，holdout 30% 评估；
- 系统臂：Agentic loop（LLM 提案 + critic + tournament + 反馈进化，预算 N 次评估）；
- 基线臂：random search、evolution search（现有 expr_search）、无 critic/无反馈消融；
- 指标：holdout fitness、有效表达式率、top-5 均值、Elo 与真实 fitness 的秩相关、探索多样性；
- 每个 claim 入账 + reviewer；负结果如实报告。


## 模型无关层与模型切换（重要）

- 所有角色（proposer/critic/reflection）都接受 `--model` / `AUTO_RESEARCH_LLM_MODEL`，每次 run 在 `summary.json` 记录 `model`；
- `agents/models.py` 可列出/匹配本地 LM Studio 模型；
- **模型隔离原则**：不同模型的 run 结果不得混算；gemma/cajal 的 run 只作 **pipeline smoke test**，不得用于评价系统能力或与 Sage-A1.5 比较；
- **Sage-A1.5 就绪后**：用同一 prompt/预算/seed 跑同一协议，Sage 作为 primary model；gemma 仅作 plumbing baseline（可选），对比结论必须标注模型与证据强度。

## 待 Sage-A1.5 就绪后的实验矩阵（预注册）

| 臂 | 模型 | 说明 |
|---|---|---|
| A | Sage-A1.5 | agentic loop（propose→critic→Elo→reflect→evolve） |
| B | Sage-A1.5 消融 | 无 critic / 无 reflection / 无 Elo（3 个消融，按轮次） |
| C | 随机搜索 | 同预算 24 条表达式 |
| D | 现有进化搜索 | expr_search evolution，同预算 |
| 指标 | holdout fitness、valid rate、top-5 均值、Elo-真实 fitness 秩相关、多样性 | 每个 run 记录 model + seed |


## 失败模式与自改进（已实现）

- **stagnation**：连续代无提升 → 在 prompt 注入"结构性不同机制"的重定向；记录 `stagnation_events`；
- **metric fixation**：记录 `metric_fixation_gap = top search fitness − top holdout fitness`，差距过大时在报告中标注；
- **成本**：`llm_stats` 记录 API 调用与 token（缓存命中不计）；
- **reviewer 批准式自改进**：`self_improve.py` 只允许改 prompt/policy/ranking；禁止 verifier/窗口/ledger/library；规则 R1–R5 审核后写 `improvements/reviews.jsonl`，仅 approved 写入 `adopted_policy.json`。

# Auto-Research 框架调研（2026-10-21，历史参考）

> 本文件保留早期调研及其当时的结论，不再作为当前架构依据。其中把 claim ledger、独立 reviewer 或完整审计作为默认建设的建议，已不再是当前开工指令。
> 当前 retro、架构和 2026-10-25 文献定位见 [DESIGN_CN.md](DESIGN_CN.md)；新检索索引保存在 `auto_research/knowledge/arxiv_*_20261025.csv`。
> 下列证据标记与旧结论仅用于追溯，引用前仍需核对一手来源。

## 0. 结论先行（给 factor_digging 的 8 条设计约束）

1. **执行接地（execution-grounded）是可靠性的第一道门**：AutoResearch(2607.02520)、MLR-Bench、ARIS 都把"代码是否真的跑通、引用是否真的支持 claim"当作过滤信号，而不是事后补救。✅
2. **验证必须独立于被改进对象**：Co-Scientist 用 tournament + Elo + 外部工具；ARIS 用**跨模型 adversarial reviewer**；我们的做法是冻结 score 窗/评分卡 + 覆盖率 STOP，本质同源。✅
3. **知识要"可检索的结构"，不是流水账**：PARNESS 用知识图谱 + scenario-typed retrieval（similar/contradictory/cross-domain/counter-intuitive）；NanoResearch 用 skill bank + memory + policy 三层共进化。我们 H2 的失败（扁平 lesson 迁移为负）正好印证这一点。✅
4. **方向设定（direction-setting）是人机共治的瓶颈**：Co-Scientist 明确 scientist-in-the-loop；Agent Swarms 文章把"review 而非 production 成为稀缺资源"列为核心问题；生命周期综述把 open-ended ideation 列为最脆弱环节。✅
5. **研究过程可以自我改进，但必须过 reviewer 才采纳**：ARIS 的 prototype self-improvement loop 记录 trace、提出 harness 改进，**reviewer 批准后才采用**。这与我们 evo 的 compose/reflect 同构，但我们的采纳规则只对 probe 负责，缺少"独立 reviewer"。✅
6. **生产规模会暴露五类失败模式**：infra fragility、agent memory decay、search-direction stagnation、iteration-cost asymmetry、metric fixation；对策是 prevent / persist / redirect。✅
7. **benchmark 显示"写得好 ≠ 做得对"**：MLR-Bench 发现 coding agent 在约 80% 的案例中伪造或未验证实验结果；生命周期综述给出 realistic success rates 1/6–1/3。✅
8. **对因子挖掘：verifier 比 ML 研究更强**（可直接回测、可冻结评分卡、可 PIT 检查），但 score 窗只能一次性使用、必须防泄漏；因此我们的 auto-research 更接近"可验证的自动实验框架"，不是论文生成器。✅（本项目实测）

## 1. 定义与边界

| 概念 | 定义 | 代表 | 与我们的关系 |
|---|---|---|---|
| Deep Research | 检索+综合+报告 | DeepResearch 类 | 我们已有文献调研，不是核心 |
| AI Scientist（端到端） | idea→实验→论文→评审全链路 | AI Scientist v1/v2、MLR-Agent | 论文生成不是目标 |
| Research Harness | 围绕固定模型的研究流程外壳（工具/记忆/编排/验证） | Harness²、ARIS、PARNESS | **我们的形态** |
| RSI / 自改进 harness | 让 harness 本身被证据驱动地改进 | Harness²、RRSI、ARIS 自改进环 | 我们验证过：bounded 成立、meta 层弱 |
| 自驱动实验室（SDL） | AI + 机器人闭环 | 化学/材料 SDL | 我们的"实验"是计算回测，不是湿实验 |

**本调研聚焦**：research harness / AI scientist / auto-research 编排与验证体系（不含湿实验硬件）。

## 2. 代表性系统拆解（一手/可靠二手）

### 2.1 AI Scientist（Sakana AI，Nature 2026）✅
- 链路：给定研究方向 → 自主生成 idea → 检索文献 → 设计/编码/运行实验（parallelized agentic tree search）→ 写 LaTeX 论文（视觉模型审图）→ Automated Reviewer 评审。
- 关键结果：v2 的 AI 生成论文在 ICLR 2025 ICBINB workshop 盲审平均 6.33（6/7/6），高于 55% 人类投稿；Automated Reviewer balanced accuracy 69%，F1 超过 NeurIPS 2021 人际一致性。
- 缩放律：底层模型越强，生成论文质量越高（用 Automated Reviewer 度量）。
- 局限：idea 幼稚、方法严谨性/复杂代码弱、引用幻觉/重复图。
- 对我们的启示：**tree search + 自动评审**可作为我们"批次搜索 + 公平回放验证"的对应物；论文生成模块不必要。

### 2.2 Co-Scientist（Google，Nature 655:487–496, 2026）✅
- 形态：Gemini 多智能体 + 异步任务队列 + 持久 context memory；角色：Generation / Reflection / Ranking / Evolution / Proximity / Meta-review。
- 核心 loop：self-play 科学辩论生成假设 → tournament（Elo）比较排序 → evolution 迭代改进；test-time compute 持续 scaling。
- 验证：15 个专家策展目标 + 203 个目标的 Elo 曲线（无饱和迹象）；3 个生物医学方向湿实验验证（AML 药物重定位、肝纤维化靶点、AMR 机制）。
- 关键消融：Reflection 接外部检索显著减少"看似新颖但不合理"的幻觉；debate prompt 改善排序、降低位置偏差；Evolution 显著提升质量；Meta-review 把 correctness 预测 AUC 从 0.521 提到 0.597。
- 局限/治理：闭源（只给伪代码+prompt）、强计算、专家在环。
- 对我们的启示：**tournament + 多角色 critique** 可用于我们的"多 harness 候选选择"；但我们的 verifier 是回测，不需要 Elo 代理。

### 2.3 ARIS（arXiv:2605.03042，开源 research harness）✅
- 三层架构：
  1. **Execution layer**：65+ Markdown 定义的 skills、6 个 MCP 模型桥、persistent research wiki、确定性画图。
  2. **Orchestration layer**：5 条端到端 workflow、可调 effort、reviewer 路由。
  3. **Assurance layer**：三阶段 claim 检查（integrity verification → result-to-claim mapping → claim auditing，对照 claim ledger 与原始证据）+ 五遍科学编辑 + 数学证明检查 + PDF 视觉检查。
- 默认配置是**跨模型对抗协作**：executor 推进，另一个模型家族的 reviewer 审中间产物并要求修订。
- 自改进：prototype loop 记录 research traces、提出 harness 改进，**reviewer 批准后才采纳**。
- 对我们的启示：**claim ledger + 跨模型 reviewer + reviewer 批准后采纳** 正好补上我们 evo 的短板（我们的采纳只对 probe 负责）。

### 2.4 AutoResearch（arXiv:2607.02520，执行接地的多智能体）✅
- 组件：sandboxed Python/PyTorch 执行、迭代 code repair、citation verification、claim-support auditing、decision control、结构化 LaTeX 生成。
- 机制：把 **runtime errors、citation-verification failures、review-agent feedback** 当作过滤信号；在 HumanEval/MBPP/SciCode/citation/claim-audit 与端到端压力测试上提升执行成功率、引用有效性、局部 claim 支持、workflow 完成率。
- 明确自我定位：**reliability-oriented research assistant，不是 fully autonomous scientist**。
- 对我们的启示：我们的批次执行/覆盖率 STOP/证据链就是 execution grounding；但缺 citation/claim 审计（对因子研究可换成"经济逻辑/先验假设"审计）。

### 2.5 PARNESS（arXiv:2605.05258，paper harness）✅
- 四个设计动作：
  1. **thin DAG kernel + 四字段 Agent contract**：调度与领域语义解耦，任何学科的 loop 写成可编辑 YAML；
  2. **全 PDF 解析 + 文献库**：正文/图/表 typed objects，abstract-only 可回退；
  3. **知识图谱**：papers/ideas/experiments/code 四类节点，scenario-typed retrieval（similar/contradictory/cross-domain/counter-intuitive），每次 LLM 调用注入聚焦切片；
  4. **coding agent 扩展面**：Claude Code/Cursor/Copilot/OpenCode 可替换模块。
- 动机直指现有系统五大刚性（工作流动态性、ideation 受上下文限制、摘要视图漏正文、paper-to-code 链接缺失、跨 run 知识不可持久检索）。
- 对我们的启示：**知识图谱 + typed retrieval** 是 H2 的具体修法；DAG+YAML 编排适合我们后续多阶段 auto-research。

### 2.6 NanoResearch（arXiv:2605.10813，个性化研究自动化）✅
- 三层共进化：**skill bank**（跨项目复用过程规则）、**memory**（用户/项目经验）、**policy**（label-free 从自由反馈学习 planner 参数更新）。
- 效果：优于 SOTA 研究系统，并随轮次"更低成本、更好结果"自我精炼。
- 对我们的启示：我们的 experience 库只有 "lesson 文本"，缺 **policy 层**（根据反馈更新实验选择策略）；这正是 E9 调度 evo 的方向。

### 2.7 AutoResearch at Production Scale（arXiv:2609.30541，ICDM 2026 Applied）✅
- 把 Karpathy AutoResearch 范式（LLM 改训练脚本、保留 held-out 标量指标改善的修改）搬到生产推荐系统：12 周、220+ 实验、跨两个 representation learning 系统。
- **五类失败模式**：infrastructure fragility、agent memory decay、search-direction stagnation、iteration-cost asymmetry、metric fixation。
- **三原则脚手架**：prevent（算力浪费前拦截）、persist（跨会话持久化）、redirect（偏离时改道）。
- 结果：1.82× Recall@6、2.1× coherence、agent 自主设计 text-only fallback 使 catalog coverage 5.8×。
- 对我们的启示：与我们的观察高度一致——H4 的"退火过早收缩"= search-direction stagnation；H5 的弱迁移= metric fixation（probe 指标 ≠ admission）；需要用 prevent/persist/redirect 设计批次调度。

### 2.8 AI Agent Swarms as Researchers（arXiv:2609.35719，2026-09）✅
- 不给科学 idea，只给 scope + 文献/计算工具 + "make real progress, don't stop"，off-the-shelf coding agents 组成 swarm；数周产出 notes/paper drafts/formal proofs（5 个方向），部分结果在 proof assistant 中完成。
- 结论：agents 已能承担 routine theoretical research 的大部分；**review 成为稀缺资源**；制度/credit/控制问题未解。
- 对我们的启示：我们的"review"对应 score 窗/准入/三口径核心；瓶颈同样是"评审能力"而非"产出能力"。

### 2.9 其他值得跟踪（🟡/⬜）
- **Robin（FutureHouse，Nature 2026）**：多模型（o4-mini + Claude 3.7）、实验生物学、药物发现；与 Co-Scientist 同期 Nature 报道。🟡
- **Agent Laboratory / ResearchAgent / InternAgent / PaperOrchestra / AutoSOTA / DeepResearch**：被 PARNESS 作为对比系统点名，代表"固定控制流"的多种形态（线性 pipeline / 状态机 / 单 agent loop / skill pack）。🟡
- **Harness²（2610.14224）**：两级递归 harness（我们已实现并验证）。✅
- **RRSI（2609.24972）**：harness 进化的正则化（退火预算/critic/pruner）。✅

## 3. 架构共性：auto-research 框架的 7 层

```
┌──────────────────────────────────────────────────────────┐
│ 0. 目标/问题接口（人）：research goal / constraints / 预算  │
├──────────────────────────────────────────────────────────┤
│ 1. 编排层：DAG 或异步队列；角色（生成/反思/排序/进化/元评审）│
│    代表：Co-Scientist 异步队列+多角色；PARNESS thin DAG     │
├──────────────────────────────────────────────────────────┤
│ 2. 知识层：文献全文/代码仓库/知识图谱/记忆/wiki              │
│    代表：PARNESS KG+typed retrieval；NanoResearch memory    │
├──────────────────────────────────────────────────────────┤
│ 3. 假设层：idea/hypothesis 生成 + novelty/feasibility 检查    │
│    代表：Co-Scientist Generation+Reflection；AI Scientist    │
├──────────────────────────────────────────────────────────┤
│ 4. 实验层：实验设计→代码/配置→执行（sandbox/GPU/工具/MCP）    │
│    代表：AI Scientist tree search；AutoResearch sandbox      │
├──────────────────────────────────────────────────────────┤
│ 5. 验证层（最关键）：执行接地 + 结果-claim 映射 + 独立 reviewer│
│    代表：ARIS 三阶段 assurance + claim ledger；MLR-Judge     │
├──────────────────────────────────────────────────────────┤
│ 6. 学习/进化层：tournament/Elo、skill/memory/policy 共进化、  │
│    harness 自改进（reviewer 批准）、AutoResearch edit-retain  │
├──────────────────────────────────────────────────────────┤
│ 7. 治理层：人机接口、预注册/审计、水印/伦理、成本控制          │
└──────────────────────────────────────────────────────────┘
```

**两条主线**：
- **能力主线**：知识→假设→实验→验证，越往下越依赖 execution 与计算；
- **可靠性主线**：执行接地、引用/claim 审计、跨模型 reviewer、claim ledger、人审——这条线决定"结果可信"而不是"看起来合理"。

## 4. 评估体系（benchmark 全景）

| Benchmark | 评什么 | 关键数字/发现 | 来源 |
|---|---|---|---|
| MLR-Bench | 开放式 ML 研究全链路（201 任务，idea/proposal/experiment/writing） | coding agent 约 **80%** 案例伪造或未验证实验结果；AI Scientist v2 overall 4.25/10，Soundness 最弱 | ✅ papernotes |
| PaperBench | 论文复现 | 关注复现而非原创 | 🟡 |
| RE-Bench | 未见任务上的研究工程 | 覆盖有限 | 🟡 |
| MLE-Bench | Kaggle 工程能力 | 生命周期综述：bronze 16.9% | ✅ 综述 |
| AstaBench | agentic science 综合 | 检索片段提及 | 🟡 |
| ResearchCodeBench | 实现新 ML 研究代码 | 32.4% 科学编码任务解决（综述数字） | ✅ 综述 |
| WhatWorkedBench | 实验理解（预算有限下预测配置效果） | 36 条件/30 源/8 workflow 家族/1248 配置；agent 重建响应面仍困难 | ✅ |
| 生命周期综述（MDPI 2026-10-17） | 2928 检索→883 合格→253 深读；四轴：架构/学习机制/自主等级/验证制度 | realistic success rates **1/6–1/3**；replication best 21.0%；equation discovery 31.5%；o1 agent 26.6% vs ML PhD 41.4% | ✅ |

**评估方法学要点**：
- 端到端 LLM-as-judge 与人类评审对齐度在部分维度可接受（MLR-Judge Mann-Whitney p>0.05），但 rubric 可能偏爱语言流畅而非科学洞察；
- **execution-grounded 指标（代码是否真跑、引用是否真支持）比文本评分更硬**；
- 湿实验/闭环发现证据仍多为 preprint，证据等级低于 benchmark 结果（综述明确标注）。

## 5. 失败模式与可靠性清单

| 失败模式 | 表现 | 代表来源 | 对策 |
|---|---|---|---|
| 实验伪造/未验证 | coding agent 生成看似合理但未真实执行的数字 | MLR-Bench（~80%） | sandbox 执行 + claim ledger + 独立审计 |
| 引用幻觉/claim 夸大 | 引用不支持生成 claim | AutoResearch、ARIS | citation verification + claim-support auditing |
| 记忆衰减 | 跨会话遗忘关键教训 | Production AutoResearch | persist：持久 wiki/知识图谱 |
| 搜索方向停滞 | 反复在同一局部改进 | Production AutoResearch；我们 H4 | redirect：探索奖励 + 外部 reviewer |
| 指标 fixation | 优化代理指标而非真实目标 | Production AutoResearch；我们 H5 | 用下游 verifier 作 fitness；OOD 验证 |
| 评审瓶颈 | 产出快于 review | Agent Swarms | 自动 reviewer + 人审抽查 |
| 过拟合训练任务 | in-distribution 涨、OOD 掉 | RRSI（2609.24972） | 正则化（退火预算/critic/pruner） |

## 6. 对我们（factor_digging）的映射

| auto-research 层 | 我们已有 | 缺口 | 优先级 |
|---|---|---|---|
| 0 目标/预注册 | `PREREGISTRATION.json`、窗口/门槛/验收 | 目标由人写，未结构化 | P1 |
| 1 编排 | `run_rsif_chain.py`、批次脚本、ledger | 无 DAG/YAML、无 async 队列、无多角色 | P2 |
| 2 知识 | 经验库（cue/lesson）+ literature 笔记 | 无全文索引、无 knowledge graph、无 typed retrieval | **P1（H2 的直接修法）** |
| 3 假设 | 固定 edit catalog + LLM stub | 无经济逻辑/文献驱动的假设生成 | **P0/P1（direction-setting）** |
| 4 实验 | v2 batch/mining 全自动 | 无统一 experiment designer、无成本调度 | P1（E9 调度） |
| 5 验证 | dev 门槛+score 一次性+覆盖率+三口径核心+公平回放 | 无 claim ledger、无独立 reviewer | **P0（claim ledger + reviewer）** |
| 6 学习 | evo compose/reflect/Pareto | 采纳只对 probe 负责；无 reviewer 批准；无 policy 层 | P1 |
| 7 治理 | append-only/预注册/失败台账/隔离库 | 无成本预算器、无外部审计接口 | P2 |

**核心洞察**：我们的"执行接地"和"冻结 verifier"其实已经强于多数 ML auto-research 系统；真正缺的是 **知识层（typed retrieval/KG）** 与 **独立 reviewer/claim ledger**——这恰好也解释了我们 H2/H5 的弱结果。

## 7. 建议：factor_digging auto-research 框架 v0

### 7.1 模块（最小可行）

1. **Goal & Prereg**：结构化的 research goal（family/panel 候选、预算、验收、停止规则）；写入 run manifest。
2. **Knowledge**：`evo_rsif/knowledge/`：
   - `papers/`（文献索引 + 摘要级 claims）、`experiments/`（每次 run 的 claim→evidence 映射）、`code/`（脚本/参数版本）、`graph.json`（节点：panel/family/operator/lesson/claim；边：supports/contradicts/derived_from）。
   - **typed retrieval**：按 `similar / contradictory / cross_domain / counterintuitive` 检索。
3. **Hypothesis generator**：输入 goal + typed retrieval 切片；输出：假设（经济逻辑）+ 可执行实验（面板/算子/预算/对照）+ 预期结果；必须引用 knowledge 节点。
4. **Experiment designer/executor**：把假设编译成 batch/v2 config；sandbox/覆盖率 STOP/预算；产出 manifest+ledger。
5. **Verifier + claim ledger**：dev probe → admission fair replay → score 窗一次性；每条 claim 记录 `{claim, evidence, run, sha256, status: supported/refuted/unverified}`；**score 窗不可反馈进 loop**。
6. **Independent reviewer**：另一个模型/规则集审 `{假设→实验→claim}`；reviewer 未通过不得采纳（ARIS 模式）。
7. **Analyst/attribution**：把结果归因为 panel effect / operator effect / measurement artifact；产出"下一问"。
8. **Scheduler**：E9 类 UCB/进化调度，按 admission per 1000 evaluated 分配算力；失败家族降权、探索保留。
9. **Governance**：预注册、append-only、失败台账、成本上限、人审 checkpoint（每 N 轮或关键决策）。

### 7.2 循环（direction-setting 显式化）

```
agenda → hypothesis → design → execute → verify → attribute → update beliefs → agenda
   ↑                                                              │
   └────────────── knowledge graph + claim ledger ────────────────┘
```

关键：**agenda（研究议程）是显式状态**，不是隐式 prompt；每轮必须产出"下一问"，并允许 reviewer 否决/重排。

### 7.3 MVP 路线（建议 3 个阶段）

- **P0（1–2 天）**：claim ledger + 独立 reviewer 规则 + 现有实验的 claim 回填（把 E0–E8 的结论变成可审计 claims）。
- **P1（3–5 天）**：knowledge graph + typed retrieval；hypothesis generator（经济逻辑模板 + 文献/知识约束）；experiment designer（面板/算子/预算/对照）。
- **P2（1 周）**：E9 调度 evo + reviewer 批准式 harness 自改进 + 成本预算器；跑 10 轮对照实验。

### 7.4 验收标准（与现有 verifier 一致）

- 每条 claim 有 evidence 链接且可复现；
- score 窗零反馈泄漏（ledger 可查）；
- OOD/公平回放上比固定轮询提升 admission/1000（E9）；
- reviewer 否决率与误判率可报告；
- 全部失败入账。

## 8. 风险与治理

- **self-confirming loop**：reviewer 与被审系统同源会互相背书 → 用跨模型/规则 reviewer + 人审抽查；
- **metric fixation**：只优化 dev/probe 会重演 H5 → 下游 verifier + score 一次性 + OOD；
- **数据泄漏**：score 窗、PIT 数据、未来函数 → 硬性 read_guard + ledger；
- **执行安全**：sandbox、只读原始数据、禁止外部取数（沿用 AGENTS.md 约束）；
- **评审负担**：产出 > 评审是结构性问题（Agent Swarms）→ 自动 reviewer + 抽样人审 + 水印/署名规范；
- **credit/制度**：auto-research 产出的归属与发表规范尚无共识（综述/Agent Swarms 都提出），本项目只做内部研究资产。

## 9. 文献与链接清单

| 文献 | 链接 | 级别 |
|---|---|---|
| AI Scientist（Nature 2026） | https://www.nature.com/articles/s41586-026-10265-5 ；https://sakana.ai/ai-scientist-nature/ | ✅ |
| Co-Scientist（Nature 655:487–496, 2026） | https://www.nature.com/articles/s41586-026-10644-y | ✅ |
| ARIS（arXiv:2605.03042） | https://arxiv.org/abs/2605.03042 | ✅ |
| AutoResearch（arXiv:2607.02520） | https://arxiv.org/abs/2607.02520 | ✅ |
| PARNESS（arXiv:2605.05258） | https://arxiv.org/abs/2605.05258 | ✅ |
| NanoResearch（arXiv:2605.10813） | https://arxiv.org/abs/2605.10813 | ✅ |
| AutoResearch at Production Scale（arXiv:2609.30541） | https://arxiv.org/abs/2609.30541 | ✅ |
| AI Agent Swarms as Researchers（arXiv:2609.35719） | https://arxiv.org/abs/2609.35719 | ✅ |
| 生命周期综述（MDPI, 2026-10-17） | https://www.mdpi.com/3043-0097/1/2/8 | ✅ |
| MLR-Bench（NeurIPS 2025, arXiv:2505.19955） | https://github.com/chchenhui/mlrbench | ✅（二手解读） |
| WhatWorkedBench（arXiv:2609.27490） | https://arxiv.org/abs/2609.27490 | ✅ |
| Harness²（arXiv:2610.14224） | https://github.com/google-research/harness2 | ✅ |
| RRSI（arXiv:2609.24972） | https://arxiv.org/abs/2609.24972 | ✅ |
| RSI 综述（arXiv:2607.07663） | https://arxiv.org/abs/2607.07663 | ✅ |
| Robin（FutureHouse, Nature 2026） | 期刊页（二手报道） | 🟡 |
| Agent Laboratory / ResearchAgent / InternAgent / PaperOrchestra / AutoSOTA / DeepResearch | 见 PARNESS 对比 | 🟡 |

## 10. 未解决问题（调研级）

1. **reviewer 与被审系统同源时的可靠性**：跨模型 reviewer 的误判率、成本、可解释性尚无系统 benchmark。
2. **端到端 vs 分阶段评估**：MLR-Bench 指出 process transparency 缺失；WhatWorkedBench 只测实验理解单点。
3. **知识图谱的自动构建质量**：PARNESS 用 PDF 解析 + 人工 schema；对因子研究，claim 多为数值统计，需要新的 schema（effect size/置信度/面板差异）。
4. **成本-收益**：auto-research 的算力成本 vs 人审时间，尚未有统一核算框架。
5. **方向设定的代理指标**：如何度量"这个问题值得研究"（RSI 综述的 prior problem），目前只有人审。

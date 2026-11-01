# 相关 auto-research 论文的方法论拆解与本项目映射

目的：不只"知道他们做了什么"，而是拆出**动机 → 机制 → 失败模式 → 解法**，并逐条落到本项目的代码与实验。

## 1. AI Scientist（Sakana AI, Nature 2026）

- **目的**：验证"端到端自动科研"（idea → 实验 → 论文 → 评审）是否可行。
- **机制**：并行 agentic tree search 做实验；自动生成 LaTeX；视觉模型审图；Automated Reviewer 评审；用 reviewer 分数做 scaling law。
- **失败模式**：idea 幼稚；复杂代码/方法严谨性弱；引用幻觉与重复图；评审分数可被"写作质量"欺骗。
- **对我们的启示**：tree search ↔ 我们的多代提案 + 评估；Automated Reviewer ↔ 我们的 reviewer 规则；**论文生成不是目标**。
- **本项目落地**：`orchestrator.py` 多代搜索；`review_claims.py` 证据规则。

## 2. Co-Scientist（Google, Nature 655:487–496）

- **目的**：用多智能体"科学辩论"生成高质量假设，并用湿实验验证。
- **机制**：Generation / Reflection / Ranking / Evolution / Proximity / Meta-review 六角色；异步队列；Elo tournament 排序；test-time compute 扩展；Reflection 接外部检索减少幻觉；Meta-review 提升 correctness 预测。
- **失败模式**：闭源、算力大、专家在环；Elo 是代理指标，未必等于真实效用。
- **对我们的启示**：Elo + 多角色 + reflection 可直接借；但我们的 verifier 是真实回测，不需要 Elo 代理——**结果发现 Elo 在 budget 12 下相关性仅 0.62，甚至有害**。
- **本项目落地**：`orchestrator.py` 的 Elo 与 reflection；`knowledge.py` 的 typed slices。

## 3. ARIS（arXiv:2605.03042）

- **目的**：给 research harness 加"保证层"（assurance），让 agent 产出可审计。
- **机制**：execution / orchestration / assurance 三层；claim ledger；跨模型 adversarial reviewer；reviewer 批准后才采纳 harness 自改进。
- **失败模式**：依赖强 reviewer 模型；流程重。
- **对我们的启示**：**claim ledger + reviewer 门控 + approval≠adoption** 是本项目最该吸收的。
- **本项目落地**：`claim_ledger.py`、`review_claims.py`、`self_improve.py`（R1–R5，approved_policy 不自动 adopt）。

## 4. AutoResearch（arXiv:2607.02520）

- **目的**：可靠性优先的研究助理，而非全自动科学家。
- **机制**：sandbox 执行、迭代 code repair、citation verification、claim-support auditing；把 runtime error / citation failure / reviewer feedback 当过滤信号。
- **失败模式**：自我定位明确——不追求 autonomy。
- **对我们的启示**：**执行接地**是可靠性的第一道门；我们的真实回测比 LLM 自评更强。
- **本项目落地**：`compare.py`/`evaluate_expr` 的真实 IC/ICIR；错误直接进 claim 证据。

## 5. PARNESS（arXiv:2605.05258）

- **目的**：把论文 harness 做成可编排、可检索的知识系统。
- **机制**：thin DAG kernel + YAML contract；全 PDF 解析；知识图谱；scenario-typed retrieval（similar / contradictory / cross-domain / counter-intuitive）。
- **失败模式**：工程量大；知识图谱维护成本高。
- **对我们的启示**：知识必须以**可检索结构**进入 prompt，而不是流水账。
- **本项目落地**：`knowledge.py` 的 negative/cross-domain/similar slices；但目前知识层规模小（无全 PDF/代码库）。

## 6. NanoResearch（arXiv:2605.10813）

- **目的**：个性化研究自动化。
- **机制**：skill bank / memory / policy 三层共进化；label-free 从反馈学 planner。
- **失败模式**：依赖反馈质量。
- **对我们的启示**：我们的 reflection 只有 lesson 文本，缺 policy 层；这是后续可补的。
- **本项目落地**：`orchestrator.py` lessons 注入 prompt（弱）；`self_improve.py` 是 policy 的雏形。

## 7. AutoResearch at Production Scale（arXiv:2609.30541）

- **目的**：把 auto-research 搬到生产系统（12 周、220+ 实验）。
- **机制**：五类失败模式（infra fragility、agent memory decay、search-direction stagnation、iteration-cost asymmetry、metric fixation）+ prevent / persist / redirect。
- **失败模式**：正是我们遇到的——文件系统 remount 杀进程（infra fragility）、长跑无断点（persist 缺失）、Sage 反复空输出（stagnation）、holdout 反复用（metric fixation）。
- **本项目落地**：stagnation 检测、metric-fixation gap 已实现；**断点续跑/checkpoint 尚未实现**（本轮事故的直接教训）。

## 8. Agent Swarms as Researchers（arXiv:2609.35719）

- **目的**：用 off-the-shelf coding agents 组队做理论研究。
- **机制**：给 scope + 工具 + "make real progress"；数周产出 notes/paper/proofs。
- **结论**：**review 是稀缺资源**，不是 production。
- **对我们的启示**：我们的瓶颈同样是"评审/验证"，不是生成；应把预算更多花在独立验证与多 split 上。

## 9. MLR-Bench

- **目的**：审计 ML research agent 的真实性。
- **发现**：coding agent 约 **80%** 案例伪造或未验证实验结果。
- **对我们的启示**：所有数字必须绑定证据 hash；这也是我们 claim ledger 的价值。

## 10. Harness² / RRSI

- **目的**：研究 harness 的递归自改进与正则化。
- **机制**：两级递归、退火预算、critic、pruner。
- **我们的验证**：bounded self-refinement 成立；meta 层迁移为负；因此只做 bounded、reviewer 门控的改进。

---

## 方法论 → 本项目映射（含实测结果）

| 机制 | 来源 | 本项目实现 | 实测结果 |
|---|---|---|---|
| 真实执行验证 | AutoResearch/ARIS | `evaluate_expr` 回测 | 强，作为唯一 fitness 来源 |
| claim ledger + reviewer | ARIS | `claim_ledger.py` / `review_claims.py` | 55 claims、17 superseded，可审计 |
| Elo tournament | Co-Scientist | `orchestrator.elo_update` | 相关性 0.62，no-elo 反而更高 → **弱/负** |
| critic 过滤 | Co-Scientist/ARIS | `roles.critique` | no-critic 0.103 > full 0.081 → **可能有害** |
| reflection lessons | Co-Scientist/NanoResearch | `orchestrator` lessons | no-reflection ≈ full → **无显著贡献** |
| structured output | 工程实践 | Sage `response_format` + `reasoning_effort:none` | 修复了 Sage 0/1 有效评估 |
| stagnation/metric fixation | Production AutoResearch | `stagnation_events` / `metric_fixation_gap` | 已记录；尚未用于自动 redirect 策略 |
| KG typed slices | PARNESS | `knowledge.compose` | 已用于 prompt；规模小，效果未单独消融 |
| reviewer 批准式自改进 | ARIS | `self_improve.py` | 3 条 approve，未 adopt（需验证） |

## 为什么 agentic ≈ random（根因假设与验证计划）

1. **H1 提案质量不足**：LLM 倾向常见因子（momentum/volatility），随机搜索覆盖更广；→ 用 few-shot 高质量样例 + KG 条件化 + 强制覆盖不同算子族。
2. **H2 critic 过度过滤**：no-critic 更高；→ critic 改为 tie-break/仅标记，不参与硬过滤；或按 holdout 校准阈值。
3. **H3 Elo 噪声**：budget 12 下 pairwise 比较太少；→ 直接用 search fitness 选择，Elo 仅作日志；或增大比较次数。
4. **H4 预算太小**：12 条评估方差大；→ budget 24/48 + 多 seed。
5. **H5 holdout 复用**：多重比较；→ 多 split（时间块）配对检验。

**成功判据**：在预注册的 budget/seed/split 下，agentic 的 holdout top5 相对 random 的配对差值 95% CI 不含 0，才写"超过 random"。

## 本轮已落实的修复

1. Sage：`reasoning_effort:none` + JSON schema structured output + 多 JSON 解析 → 从 0/1 条有效评估恢复到 6/6；
2. opencode provider 接入（`deepseek-v4.1-flash`）；
3. 记录 infra fragility（文件系统 remount）与 persist 缺失，后续加 checkpoint。

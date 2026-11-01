# auto_research 研究议程（历史归档）

> 2026-10-25 起，本文件的 A1–A6 仅作历史记录，不得据此重启旧实验。当前研究问题与实施顺序见 [DESIGN_CN.md](DESIGN_CN.md)。
> 后续不以 claim、reviewer 或完整证明链作为每轮必需产出；失败与 null 仍在原始 run 中保留。

## 历史优先级（A1–A6，均已完成）

| ID | 问题 | 为什么重要 | 计划动作 | 状态 |
|---|---|---|---|---|
| **A1** | 在线调度：引入随机候选/面板子集后，UCB 是否仍优于固定轮询？ | 当前离线评估只是确定性的 policy replay，无法回答"在线学习"是否有效 | 随机候选子集 + 真实批次；UCB vs round_robin，2 seeds × 6 轮 × 4 面板，max_candidates=3 | **done（supported，+55.56/12 轮；弱效应，见 A1_ONLINE_SCHEDULER_CN.md）** |
| **A2** | 同域经验记忆（price_volume）是否有效？ | H2 的负结果来自跨域迁移；同域尚未有干净对照 | 用已有 e2a/e2b 提取 claim | **done（CLM-A2-20261021-001 approved，无增益）** |
| **A3** | 哪些面板族 intrinsic yield 更高？排序是否稳定？ | 直接决定挖掘算力分配（vp18b +36 vs 多批 0 的教训） | 扩展 panel_table 到 ≥10 个面板族；fair-replay added/1000；报告排序与 bootstrap CI | **done（10 面板；top=alternative_features_4 166.67/1000；单批次点估计，无 seed 重复）** |
| **A4** | KG 文献/代码层能否改善 hypothesis 生成？ | H2 负结果提示知识层太薄；PARNESS/ARIS 都用 KG+typed retrieval | 增加 paper/code 节点与 informs 边；hypothesis generator 引用 KG 节点并记录 | **done（paper 10 + code 8；KG 120 nodes / 205 edges）** |
| **A5** | 无外部模型 API 时，reviewer 如何避免"自审自过"？ | rules-v0 同源自审风险 | 规则对抗自检 + 证据交叉检查；如实标注"无独立模型"限制；记录拒/过原因 | **done v0.3（R7 证据不可变性；原 PROP-001 claim 因 jsonl 证据被拒，V2 快照版 approved）** |
| **A6** | 是否把 auto-research 用在真实挖掘（新面板/新家族）而不是只做方法学？ | 最终价值取决于真实库增长 | 选择高 yield 面板（alternative_features_4）跑 base vs evolved 公平回放 | **done（EXP-20261021-003：not_supported，evolved 83.33 < base 166.67）** |

## 已完成（从 RSI/auto_research 前两轮继承）

- P1：KG/typed retrieval/hypothesis/experiment runner；EXP-20261021-001 负结果（组合 harness 未迁移到 joint_mining_r2）。
- P2：panel_table（6 面板）、离线调度评估（greedy > ucb1 > ε-greedy > round_robin）、reviewer 批准式 harness 提案（PROP-001 候选层验证 supported）、成本预算器。
- 结论边界：bounded self-refinement 成立；meta-level RSI 无证据；H5 弱正混合。

## 决策规则

1. 每轮只推进一个 AGENDA item（可并行小任务），产出 claim + reviewer 审核；
2. 若某 item 连续 2 轮无进展或证据不足 → 标 `blocked` 并记录原因；
3. 新问题只能追加到 AGENDA（append-only），不得删除历史；
4. 任何涉及 score 窗的动作必须标记 `score_window_read=true` 并停止（预注册禁止 loop 内读取）。

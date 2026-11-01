# 论文就绪评估（PAPER READINESS）——写论文前暂停

日期：2026-10-21（goal "推进 research 至写论文前暂停"）
状态：**research 继续中**（本文件是就绪清单与缺口台账，不是论文稿）

## 1. 候选论文与定位

**主论文 P1（方法+实证）**：
> *Evidence-grounded bounded self-refinement for alpha factor mining: what transfers, what doesn't*

- 核心主张：在冻结 verifier + 公平回放 + claim ledger 的约束下，有界自我改进（bounded self-refinement）在 dev 窗稳定为正；但经验记忆、harness 进化、跨域/跨面板迁移**没有**可复现的增量，且负结果可被系统性审计。
- 卖点不是"发现新因子"，而是"**可审计的负面/边界结论 + 方法**"：7-seed 递归、2 面板 OOD、多面板公平回放、单算子消融、随机化调度、10 面板 yield 排序。

**备选论文 P2（系统）**：auto_research 过程系统（KG + typed retrieval + hypothesis/designer/reviewer/ledger），以 P1 的负面结果作为案例研究。

**当前选择：P1 为主**；P2 作为 P1 的 methods/系统章节素材。

## 2. Contributions → Claims 映射

| # | Contribution | 证据（claims/artifacts） | 强度 |
|---|---|---|---|
| C1 | 有界自我改进稳定为正（7 seeds，plateau ≤4 轮） | CLM-20261021-0001 | 强（7 seeds） |
| C2 | 经验记忆/LLM 提议无增量（跨域 + 同域） | CLM-20261021-0002、CLM-A2-20261021-001 | 强（多设置一致） |
| C3 | OOD 迁移为正但**非可加**（单算子为负；Jaccard 0.33–0.66） | CLM-20261021-0003 | 中强 |
| C4 | 正则化需要 exploration reward 才有效 | CLM-20261021-0004 | 中 |
| C5 | 真实库增长弱正/混合（fundamental/r23 正、joint 负；score 窗历史最终验证无一致正增益） | CLM-20261021-0005（score_window_read=true，历史一次性）、CLM-EXP-001/003 | 中（含唯一分数窗验证） |
| C6 | harness 进化不迁移到未见面板/高 yield 面板（3 面板 × 3 随机候选子集复现，平均 Δ -74.07） | CLM-EXP-20261021-001、CLM-EXP-20261021-003、CLM-REPL-TRANSFER-001 | **强（多 seed/多面板一致）** |
| C7 | 面板 intrinsic yield 排序（10 面板，top alt4 166.67/1000） | CLM-A3-PANEL-YIELD-001 | 中（单批次） |
| C8 | 在线调度：小 cap 随机化下 2-seed 曾显示 UCB > RR，但 **seed 19 复现后 3-seed 持平（444.44 vs 444.44）**；生产 cap 随机化惰性 | CLM-A1-ONLINE-SCHEDULER-001、SEED19、AGG-001-V2 | 弱（结论不稳健，作为方法学警示） |
| C9 | 治理：reviewer 规则 + 证据快照 + 负结果入账 | CLM-20261021-0006/0007/0008、A5 规则 | 强（过程可复现） |

## 3. 论文骨架（outline，仅规划）

详细提纲见 [PAPER_OUTLINE_CN.md](PAPER_OUTLINE_CN.md)（章节/图表/claims 映射；正文未写）。

1. Introduction；2. Related work；3. Method；4. Experimental setup；
5. Results（C1–C9）；6. Negative results & failure analysis；7. Threats/limitations；8. Conclusion；9. Appendix。

## 4. 图表数据计划（写论文时生成，不现在写）

- Table 1：claims → evidence 映射（`ledger/claims.jsonl`）。
- Table 2：10 面板 yield 排序（`scheduler/panel_table.json`）。
- Table 3：multi-seed replication（`scheduler/replication_result.json`，待跑）。
- Fig 1：7-seed utility 轨迹（`evo_rsif/runs/rsif_e1_s*`）。
- Fig 2：OOD $\Delta$ 分布（`e3_*`/`e7_*` 结果）。
- Fig 3：在线调度累计 reward（`scheduler/online_result.json`）。
- Fig 4：KG 结构（`knowledge/graph.json`）。

## 5. 写论文前缺口（GAP）

| gap | 状态 |
|---|---|
| G1 多面板 × 多随机候选子集复现"evolved 不迁移" | **done**（平均 Δ -74.07/1000，3/3 面板为负；CLM-REPL-TRANSFER-001 approved） |
| G2 A1 第三个 seed 复现（seed 19） | **done**（UCB 333.33 < RR 444.44；3-seed 汇总持平，AGG-001-V2 approved） |
| G3 score 窗一次性最终验证 | **已在历史 RSI 阶段执行一次**（`archive/.../SCOREWINDOW_VERIFICATION_CN.md`，claim 0005）；本阶段不再读、协议文档记录禁止再读 |
| G4 图表数据冻结脚本 | **done**（`scripts/build_paper_tables.py` → `paper_assets/`，含 claims/panel/replication/online/trajectory CSV） |
| G5 外部 reviewer（跨模型） | 无法执行（无外部模型 API），如实标注 |

## 6. 暂停判据（写论文前）——**全部满足（pause 达成）**

- [x] G1 完成并 claim approved（CLM-REPL-TRANSFER-001-V2，3/3 面板负）；
- [x] G3 决策记录：**已使用过（历史一次性）**，本阶段不再读（`SCOREWINDOW_ONESHOT_PROTOCOL_CN.md`）；
- [x] G4 图表数据可一键重生成（`build_paper_tables.py` → `paper_assets/`）；
- [x] 本文件 §2/§5 与 ledger 一致（claim verify 通过）；
- [x] 不写论文正文（仅 readiness/outline/数据冻结；本 goal 到此暂停）。

**Pause 状态**：research 已推进到"只剩写论文"的点；论文正文、摘要、投稿材料均未开始。

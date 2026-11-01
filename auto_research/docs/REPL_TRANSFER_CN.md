# C6 强化：多面板 × 多随机候选子集复现"evolved harness 不迁移"

- 设计：3 面板 × 3 随机候选子集（cap=5，candidate-seed ∈ [101, 202, 303]）× 2 arms；奖励 = fair-replay added/1000（v2 快照）；

| panel | base 平均 | evolved 平均 | Δ（evolved−base） |
|---|---|---|---|
| joint_r2 | 155.55 | 22.22 | **-133.33** |
| fundamental_stability | 66.67 | 44.45 | **-22.22** |
| fundamental_indicator | 177.78 | 111.11 | **-66.67** |

**汇总**：平均 Δ = **-74.07** /1000；evolved 在全部 3 个面板都更差 → verdict **supported**；

**结论**：H3 的组合/预算重排效应**不能**迁移到未见面板族；EXP-20261021-001/003 的单次负结果被多面板多 seed 复现强化。

证据：`scheduler/replication_result.json`；claim `CLM-REPL-TRANSFER-001`（approved）。

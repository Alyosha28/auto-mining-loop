# A3：面板族 intrinsic yield 排序（fair-replay added/1000，v2 快照）

- 面板数：10（全部 COMPLETED）；每面板一个 base-harness 批次，max_candidates=40；公平回放从 v2 快照（size 1）；score 窗未读。

| rank | panel | evaluated | passed | added | added/1000 |
|---|---|---|---|---|---|
| 1 | alternative_features_4 | 12 | 3 | 2 | 166.67 |
| 2 | fundamental_indicator | 120 | 31 | 17 | 141.67 |
| 3 | joint_mining_r2 | 42 | 7 | 5 | 119.05 |
| 4 | alternative_features_5 | 18 | 3 | 2 | 111.11 |
| 5 | fundamental_stability | 57 | 8 | 5 | 87.72 |
| 6 | r23_extension | 45 | 3 | 2 | 44.44 |
| 7 | joint_mining_r1 | 24 | 2 | 0 | 0.0 |
| 8 | market_regime_v2 | 12 | 0 | 0 | 0.0 |
| 9 | joint_limit_suspension | 15 | 1 | 0 | 0.0 |

**最高**：alternative_features_4（166.67/1000）> fundamental_indicator（141.67）> joint_mining_r2（119.05）> alternative_features_5（111.11）；
**最低/零产出**：fundamental_stability 87.72、r23 44.44、market_regime_v2 0.0、joint_mining_r1 0.0、joint_limit_suspension 0.0、fundamental_comovement 0 候选。

**限制**：每面板只有 1 个批次（无 seed 重复），因此排序是点估计；bootstrap CI 不可识别（同一批次无重采样维度）。
**运维发现**：`joint_limit_suspension` 含 boolean 特征，原 `make_variants` 对 bool 做减法报错；已在 v4 加 `astype(float64)` 修复（向后兼容），该面板随后可跑通（15 evaluated / 1 passed / 0 added）。

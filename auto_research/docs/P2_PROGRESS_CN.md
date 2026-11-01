# P2 进度（1）：调度实验 + reviewer 批准式 harness 自改进

日期：2026-10-21（auto_research goal round 2）

## 1. 调度实验设计（fitness = fair-replay admission per 1000 evaluated）

- 面板池（6）：fundamental_stability、joint_mining_r2、fundamental_comovement、r23_extension、fundamental_indicator、joint_mining_r1；
- 每个面板用 **base harness**（`rsif_e1_s7/round_01/base.json`）、max_candidates=40 跑一个批次；
- fair replay：每个批次从 **v2 快照（size 1）** 重放准入，得到"内在产出"（避免用当前 v9 导致多数面板新增为 0 的库增长混杂）；
- 成本模型：3 分钟/批次；预算 = rounds × 3 分钟（脚本记录）。

## 2. 策略

| 策略 | 说明 |
|---|---|
| `round_robin` | 固定轮询（基线） |
| `greedy` | 只用历史最优面板（不探索） |
| `ucb1` | 置信上界（探索+利用） |
| `epsilon_greedy` | ε=0.2 |

**诚实声明（重要）**：batch mining 对 (panel, harness, budget) 是确定性的，**batch 级不存在实验 seed**；
因此 P2 用**面板级 bootstrap 重采样**作为 seeds（7/11/17），结论标注为 **offline policy evaluation**，而不是随机对照实验。
若要真实随机性，需要引入随机候选生成/随机面板子集，这是后续工作。

## 3. reviewer 批准式 harness 自改进（v0.1）

- 提案 schema：`edit`（可编辑面内）+ `expected_metric` + `falsification` + `evidence_claims` + `score_window_read=false`；
- reviewer rules-v0.1：R1 合法编辑（复用 `validate_proposal`）、R2 引用 approved claims、R3 预期指标/阈值/证伪、R4 score 窗零读取、R5 intent 必填；
- 首个提案 **PROP-20261021-001**（append `ema5`，目标降低 turnover_proxy）**已 approve**；仅写入 `harness_improvements/adopted_harness.json` 记录，**不修改冻结 verifier、不改 evo_rsif 证据**；
- 待办：把 approved 提案接入一次真实 evo step 的候选生成（需要新 run，故放到后续轮次）。

## 4. 状态

- 面板数据采集中（6 批次 + 6 次 fair replay）；
- 完成后运行 `run_scheduler_experiment.py` 写入 `scheduler/scheduler_result.json` 并生成 claim（离线评估）；
- 负结果/零产出面板将如实入账（scheduler 的 reward 表本身会暴露 yield=0 的面板）。


## 5. 结果（Round 2 末）

### 面板表（fair replay 从 v2 快照，base harness，max 40）

| panel | evaluated | passed | added | added/1000 | 备注 |
|---|---|---|---|---|---|
| fundamental_stability | 57 | 8 | 5 | 87.72 |  |
| joint_mining_r2 | 42 | 7 | 5 | 119.05 |  |
| r23_extension | 45 | 3 | 2 | 44.44 |  |
| fundamental_indicator | 120 | 31 | 17 | 141.67 |  |
| joint_mining_r1 | 24 | 2 | 0 | 0.0 |  |
| fundamental_comovement | 0 | 0 | 0 | None | NO_CANDIDATE: 0 candidates passed dev screen (panel unusable at current thresholds) |

### 离线策略评估（12 轮，panel-level bootstrap seeds [7, 11, 17]）

| policy | 累计 admission/1000 均值 | 范围 | 累计 regret 均值 |
|---|---|---|---|
| round_robin | 903.11 | 873.48–917.92 | 796.93 |
| greedy | 1700.04 | 1700.04–1700.04 | 0.0 |
| ucb1 | 1188.15 | 1188.15–1188.15 | 511.89 |
| epsilon_greedy | 1134.96 | 928.59–1330.62 | 565.08 |

**结论**：在 5 个有效面板的离线评估中，**greedy（只选历史最优）> ucb1 > epsilon_greedy > round_robin**；
由于 batch mining 确定性，这是 **offline policy evaluation**，不能直接外推为"在线调度收益"。
`fundamental_comovement` 面板 0 候选通过 dev screen（NO_CANDIDATE），已在面板表标注，不纳入策略评估。


## 6. PROP-001 候选层验证（已完成）

把已 approve 的 PROP-20261021-001（append `ema5`）应用到 base harness，与未修改 harness 做匹配 evo step
（同 seed/budget/probe；无 score 窗读取），结果：

| metric | Δ（modified − base） | 规则 |
|---|---|---|
| turnover_proxy | **-0.0578** | 目标 ≤ −0.02 |
| icir_p25 | **+0.0360** | 不得低于 −0.01 |
| mean_abs_ic | -0.0003 | — |
| top_ic_mean | -0.0031 | — |

**verdict: supported**；claim `CLM-PROP-20261021-001-V2`（含不可变证据快照）已 approve。
证据：`harness_improvements/verification/PROP-20261021-001/`。

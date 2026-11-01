# Agentic Auto-Research 评估报告（自动生成）

更新时间：2026-10-22T07:59:01.637487+00:00

> 注意：mock proposer 的矩阵只验证 pipeline，不是模型能力结论；不同模型不得混算。

## 实验矩阵

### deepseek-flash（budget 12，seeds [7, 11, 17]，proposer=llm）

| arm | holdout top5 mean | Elo↔fitness Spearman |
|---|---|---|
| random | 0.077542 | — |
| evolution | 0.030795 | — |
| agentic_full | 0.081056 | 0.6177 |
| agentic_no_critic | 0.10275 | 0.7739 |
| agentic_no_reflection | 0.080309 | 0.8252 |
| agentic_no_elo | 0.091629 | 0.1072 |

### deepseek-v4.1-flash（budget 12，seeds [7, 11, 17]，proposer=llm）

| arm | holdout top5 mean | Elo↔fitness Spearman |
|---|---|---|
| random | 0.077542 | — |
| evolution | 0.030795 | — |
| agentic_full | 0.025728 | 0.9138 |
| agentic_no_critic | 0.003237 | 0.9231 |
| agentic_no_reflection | -0.006582 | 0.8531 |
| agentic_no_elo | -0.000746 | 0.4685 |

### sage-a1.5（budget 12，seeds [7]，proposer=None）

| arm | holdout top5 mean | Elo↔fitness Spearman |
|---|---|---|
| random | 0.076742 | — |
| evolution | 0.077377 | — |
| agentic_full | 0.053277 | 0.8 |

### sage-a1.5（budget 12，seeds [7, 11, 17]，proposer=llm）

| arm | holdout top5 mean | Elo↔fitness Spearman |
|---|---|---|
| random | 0.077542 | — |
| evolution | 0.030795 | — |
| agentic_full | 0.02911 | 0.8298 |
| agentic_no_critic | 0.005373 | 0.9441 |
| agentic_no_reflection | 0.047469 | 0.9021 |
| agentic_no_elo | 0.025131 | 0.5734 |

## 证据绑定（manifests / analyses）

| manifest | result hash ok | score window |
|---|---|---|
| ._matrix_deepseek-v4.1-flash.manifest.json | False | None |
| ._matrix_sage-a1.5.manifest.json | False | None |
| matrix_deepseek-flash.manifest.json | True | False |
| matrix_deepseek-v4.1-flash.manifest.json | True | False |
| matrix_local_sage-a1.5_partial_seed7.manifest.json | True | False |
| matrix_sage-a1.5.manifest.json | True | False |

- analysis files: analysis_matrix_deepseek-flash.json, analysis_matrix_gemma-4-26b-a4b-styletune-qk-heretic-i1.json, analysis_matrix_local_sage-a1.5_partial_seed7.json

## 状态

- 已有矩阵：deepseek/deepseek-flash, opencode/deepseek-v4.1-flash, local/sage-a1.5, local/sage-a1.5；
- 本地 Sage 矩阵：已完成；
- gemma/cajal：仅 plumbing smoke test，不参与能力比较；
- 边界：本地数据/本地模型；score 窗未读；新时间窗未重读；主库/evo 库未混入。

## 复现命令

```bash
.venv/bin/python auto_research/agents/run_matrix.py --model <model-id> --seeds 7,11,17 --budget 24
.venv/bin/python auto_research/agents/build_report.py
```

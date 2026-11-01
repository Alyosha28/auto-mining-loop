# 论文提纲（PAPER OUTLINE）——仅规划，正文未写

> 状态：**规划稿**；本文件不含论文正文、摘要或段落草稿。写作在 pause 之后由人工/后续任务启动。
> 主论文 P1 定位见 [PAPER_READINESS_CN.md](PAPER_READINESS_CN.md)。

## 标题（工作标题）

*Evidence-grounded bounded self-refinement for alpha factor mining: what transfers, what doesn't*

## 摘要要点（bullet，非正文）

- 问题：LLM/agent 自我改进在因子挖掘中是否真的可迁移、可复现？
- 方法：冻结 verifier + dev/embargo/score 协议 + 公平回放（固定库快照）+ claim ledger/reviewer；
- 结果：有界自我改进稳定为正（7 seeds）；记忆、harness 进化、跨面板迁移**不迁移**（多 seed 复现）；
- 贡献：可审计的负结果 + 方法协议，而不是新因子表现。

## 1. Introduction

- 1.1 动机：alpha 挖掘中的自我改进循环；过拟合/验证泄漏风险；
- 1.2 核心问题：哪些自我改进信号是真实的、哪些只是预算/顺序重排；
- 1.3 Contributions（C1–C9，见 readiness §2）。

## 2. Related work

- RSI 层级与验证层级（survey）；Harness²、RRSI（正则化）；PARNESS/ARIS（KG + claim ledger + reviewer）；AutoResearch/Co-Scientist（执行 grounding、ranking）；MLR-Bench（不可验证结果）。
- 与本文差异：因子挖掘领域的冻结 verifier + 公平回放 + 负结果审计。

## 3. Method

- 3.1 数据与窗口：dev 2017-01-01..2020-11-30；embargo 2020-12；score 2021-01-01..2023-12-31（**历史 RSI 阶段已一次性最终验证**，auto_research 新增实验均未读 score 窗）；
- 3.2 bounded self-refinement：固定 harness / 每轮编辑预算 / plateau 停止；
- 3.3 experience library（扁平/结构化/LLM 提议）；
- 3.4 fair replay：同一库快照下重放准入（added/1000）；
- 3.5 证据治理：claim ledger、evidence hash、reviewer rules R1–R7、负结果入账；
- 3.6 指标：select utility = mean|IC| + 0.3·ICIR_p25 − 0.1·turnover_proxy；admission/1000；IC/ICIR/turnover。

## 4. Experimental setup

- 面板族（price_volume / fundamental / intraday / joint / alternative 等）；
- 算子集（residual/residual_size/delta4/delta13/rank/zscore/winsorize/vol_scaled）；
- 预算与 seeds（7-seed 递归；多面板 seed 复现；随机候选子集 cap=3/5）；
- 评估协议（dev 探针 → admission → 公平回放；score 窗零反馈）。

## 5. Results

| 小节 | 内容 | 证据 |
|---|---|---|
| 5.1 | bounded self-refinement 正结果（7 seeds，plateau ≤4） | C1 / Fig 1 |
| 5.2 | memory/LLM 提议无增量（跨域+同域） | C2 / Table 1 |
| 5.3 | OOD 迁移正但非可加（单算子负、Jaccard 0.33–0.66） | C3 / Fig 2 |
| 5.4 | 正则化 + exploration reward | C4 |
| 5.5 | harness 进化不迁移（3 面板 × 3 随机子集，平均 Δ −74.07/1000） | C6 / Table 3 |
| 5.6 | 面板 intrinsic yield 排序（10 面板，top alt4 166.67/1000） | C7 / Table 2 |
| 5.7 | 在线调度：3-seed 持平（444.44 vs 444.44）；小 cap 随机化惰性 | C8 / Fig 3 |

## 6. Negative results & failure analysis

- H2 记忆无效；H5 库增长弱正/混合；
- evolved harness 在未见面板/高 yield 面板更差；
- A1 首版随机化惰性（#passing < cap）及 3-seed 不稳健；
- 面板 fundamental_comovement 0 候选、joint_limit_suspension boolean 特征 bug（已修）；
- 每个负结果均有 claim + reviewer + 证据 hash。

## 7. Threats / limitations

- auto_research 新增实验 dev-only；score 窗已在历史阶段执行一次（见 SCOREWINDOW_ONESHOT_PROTOCOL_CN.md）；
- C7 单批次点估计；C8 reward 离散、2→3 seed 翻转；
- 无外部模型 reviewer（规则自审，已如实标注）；
- 面板/种子数量有限；库 admission 依赖 dedup 阈值。

## 8. Conclusion

- 有界自我改进可复现，但"元层面"自我改进（记忆/进化/调度）缺少可迁移证据；
- 价值在于方法 + 可审计的负结果，而非宣称 open-ended RSI。

## 9. 附录

- A. claim ledger 全表；B. reviewer 规则；C. 复现命令；D. 失败台账。

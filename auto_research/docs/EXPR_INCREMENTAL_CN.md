# 增量表达式搜索（残差目标）——设计

背景：前一版按 standalone IC 搜索，结果否定了"反馈搜索优于随机/现有特征"。
本版把"更好"重新定义为**增量信息**：候选因子应解释现有面板因子**没有解释**的收益方差。

## 协议（预注册）

- 窗口：dev 2017-01-01..2020-11-30；搜索窗 = 前 70%（2017-01..2019-09），holdout = 后 30%（2019-09..2020-11）；score 窗零读取。
- 基模型：Ridge（alpha=1.0）对现有面板特征（截面 rank-pct）预测 forward_return（截面 rank-pct）；
  - 搜索窗用 **5 折时间块 OOF 残差**做候选选择目标（避免用 in-sample 残差）；
  - holdout 残差 = 用全部搜索窗拟合的基模型在 holdout 上的 y − pred。
- 候选评估：表达式值 vs 残差的逐周 rank IC；搜索 fitness = |mean IC| + 0.3·|ICIR|（对残差）；
  最终比较 = top5 的 **holdout 残差 IC**。
- A/B：反馈进化（3 代×20） vs 随机搜索（60 条），同预算；baseline = 现有面板特征自身的 holdout 残差 IC（top5）。
- 判定：best generated top5 holdout residual IC 比 baseline 高 ≥0.005 → generated_better_incremental；否则 not_supported。
- 产物：`auto_research/expr_search/results/incremental_*.json`、claim `CLM-EXPR-INCR-<seed>`。

## 备注

- 这是"因子组合/增量"视角；standalone IC 低但增量 IC 正的因子在组合中有价值。
- 若仍为负，则自动搜索在 dev 窗内没有找到超过现有特征的增量信息（负结果入账）。


## 结果（seed 1）

| 模式 | top5 search fitness（残差） | top5 holdout 残差 IC | pass holdout |
|---|---|---|---|
| random | 0.056117 | -0.000308 | 26 |
| evolution | 0.046617 | 0.001341 | 16 |
| baseline（现有特征） | 0.0382 | 0.004622 | 29 |

- best generated vs baseline Δ = **-0.003281** → **not_supported**；
- 解读：所有模式的 holdout 残差 IC 都接近 0（−0.0003 ~ +0.0046）；现有特征自身的增量 IC 也仅 +0.0046——
  说明在 Ridge 基模型下，dev holdout 残差几乎不可预测，搜索没有找到"增量信息"。


## 3-seed 汇总

| seed | evolution holdout | random holdout | baseline | Δ best-vs-baseline |
|---|---|---|---|---|
| 1 | 0.001341 | -0.000308 | 0.004622 | -0.003281 |
| 2 | 0.002476 | -0.001597 | 0.004622 | -0.002146 |
| 3 | 0.006836 | 0.002917 | 0.004622 | 0.002214 |

- evolution 平均 0.003551，random 0.000337，baseline 0.004622；
- evolution 在 3/3 seed 上高于 random（小幅度），但 best generated 平均 Δ vs baseline = -0.001071；
- **verdict: not_supported**（claim `CLM-EXPR-INCR-AGG-001` approved）。

**解读**：残差目标下所有因子（含现有特征）的 holdout 增量 IC 都接近 0；进化相比随机有一致的微弱优势，但不足以超过现有特征——在 Ridge 残差定义下，"自动找到更好因子"仍不成立。这也提示：dev 窗内现有 85 个特征 + Ridge 已基本耗尽可预测性，或该残差目标本身噪声过大。

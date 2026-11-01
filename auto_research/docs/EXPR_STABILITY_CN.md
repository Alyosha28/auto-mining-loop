# 稳定性正则化表达式搜索（第三范式）

背景：v2（standalone IC）与 v3（残差增量）都显示反馈搜索在 holdout 上不优于随机/现有特征，
主要失败模式是**搜索过拟合**。本版在**选择目标**上直接加稳定性约束。

## 协议（预注册）

- 窗口：dev 2017-01-01..2020-11-30；search = 前 70%（2017-01..2019-09），holdout = 后 30%（2019-09..2020-11）；score 窗零读取。
- 选择目标（search 窗内）：按日历年块计算 |IC|，`stability = mean(|year IC|) − 0.5·std(|year IC|)`；
- 最终比较：holdout 上用标准 fitness（|IC| + 0.3·|ICIR| − 0.1·turnover）评估 top5；
- A/B：反馈进化（3 代×20，按 stability 选择） vs 随机搜索（60 条，同一 fitness）；baseline = 现有面板特征（按 stability 选 top5，比较 holdout fitness）；
- 判定：best generated top5 holdout fitness 比 baseline 高 ≥0.005 → supported；否则 not_supported；
- 产物：`expr_search/results/stability_*.json`、claim `CLM-EXPR-STAB-<seed>`。

## 预期与判据

- 若稳定性选择有效：进化 top5 的 holdout fitness 应超过 random 且接近/超过 baseline；
- 若仍为负：说明"过拟合"不是唯一问题，自动搜索在本 dev 窗数据上没有可用增量信号（负结果入账）。


## 结果（3 seeds）

| seed | evolution holdout | random holdout | baseline（现有特征） | Δ best-vs-baseline |
|---|---|---|---|---|
| 1 | 0.110493 | 0.100699 | 0.137088 | -0.026595 |
| 2 | 0.107007 | 0.115006 | 0.137088 | -0.022082 |
| 3 | 0.102476 | 0.10119 | 0.137088 | -0.034612 |

- 平均：evolution 0.106659、random 0.105632、baseline 0.137088；
- best generated vs baseline 平均 Δ = **-0.027763** → **verdict: not_supported**（claim `CLM-EXPR-STAB-AGG-001` approved）；
- 解读：稳定性正则化没有改变结论——进化 ≈ 随机，且都明显低于现有特征 baseline；"搜索过拟合"不是唯一原因，当前 dev 窗自动搜索没有找到超过现有特征的因子。

# 组合搜索（第四范式）：现有特征的 rank 组合

背景：前三范式（standalone / 残差增量 / 稳定性正则化）均未找到超过现有特征的因子。
本版测试"系统能否通过**组合**现有特征找到更好因子"——组合是"更好因子"的最后一种合理定义。

## 协议（预注册）

- 窗口：dev 2017-01-01..2020-11-30；search = 前 70%（2017-01..2019-09），holdout = 后 30%（2019-09..2020-11）；score 窗零读取。
- 候选：2–5 个现有面板特征的 **rank 平均**（截面 rank-pct 后等权平均）；
- 选择：search 窗标准 fitness（|IC|+0.3|ICIR|−0.1·turnover）；
- A/B：反馈进化（3 代×20，子集加/删/换） vs 随机子集（60 个），同预算；
- baseline：现有单因子（同 fitness 选 top5，holdout 比较）；
- 判定：best generated top5 holdout fitness 比 baseline 高 ≥0.005 → supported。
- 产物：`expr_search/results/combo_*.json`、claim `CLM-EXPR-COMBO-<seed>`。

## 结果

（运行后填充；若仍为负，则四个范式一致否定"当前 dev 窗自动搜索能找到更好因子"。）


## 结果（3 seeds，holdout）

| seed | evolution holdout fitness | random holdout fitness | baseline 单因子 | Δ best-vs-baseline | evolution holdout IC |
|---|---|---|---|---|---|
| 1 | 0.179506 | 0.152046 | 0.133362 | 0.046144 | 0.059689 |
| 2 | 0.144192 | 0.143505 | 0.133362 | 0.01083 | 0.048122 |
| 3 | 0.158225 | 0.135177 | 0.133362 | 0.024863 | 0.055006 |

- 平均：evolution **0.160641**、random 0.143576、baseline 0.133362；
- best generated vs baseline 平均 Δ = **+0.027279**；3/3 seed 为正；
- **verdict: supported**（claim `CLM-EXPR-COMBO-AGG-001` approved）；
- evolution 在 3/3 seed 上高于 random（seed2 仅 +0.0007，很弱）。

### 解读与限定

- 正结果主要来自**组合本身**：即使 random 组合（0.1436）也高于最佳单因子（0.1334）——rank 组合降低了单因子噪声/换手；
- 反馈进化的增量是**弱正且不稳定**（seed2 几乎持平）；
- 这是 **dev holdout（2019-09..2020-11）** 结果，不是 score 窗 OOS；"更好"= 该 dev 窗复合 fitness（IC/ICIR/turnover），且是"复合因子"而非常规单因子表达式；
- 四个范式中只有这个为正，存在多重比较风险，应视为探索性结论，需独立复现（更多 seeds/时间折）后才能写成强主张。


## 稳健性（额外 seeds + 时间切分）

| split | seeds | evolution | random | baseline | mean Δ | positive |
|---|---|---|---|---|---|---|
| frac_70_30 | 6 | 0.161926 | 0.15122 | 0.133362 | +0.02916 | 6/6 |
| holdout_2020 | 3 | 0.147276 | 0.141964 | 0.123274 | +0.029268 | 3/3 |
| holdout_2019_2020 | 3 | 0.196765 | 0.179985 | 0.162242 | +0.037435 | 3/3 |

- **3/3 切分 mean Δ > +0.005** → verdict **robust**（claim `CLM-EXPR-COMBO-ROBUST-001` approved）；
- evolution 高于 random：**9/12** seed-split；
- 最终报告：`docs/EXPR_SEARCH_FINAL_REPORT_CN.md`。

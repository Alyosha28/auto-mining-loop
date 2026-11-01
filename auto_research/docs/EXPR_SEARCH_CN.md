# 因子表达式搜索：反馈驱动 vs 随机（dev 窗 A/B）

目标：检验"自动研究系统能否自动找到更好的因子"——用**表达式搜索**作为可执行载体，
在 dev 窗内做同预算 A/B：反馈驱动进化（3 代 × 20） vs 随机搜索（60 条表达式）。

## 协议（预注册）

历史预注册未纳入公开快照；以下记录保留当时的协议口径：
- 窗口：dev 2017-01-01..2020-11-30；embargo 2020-12；**score 窗从不读取**；
- 原语：本地面板特征（local_quote_library 33 个 + price_volume 采样 pv_*）；
- 语法：leaf → 一元算子（rank/zscore/winsorize/vol_scaled/tanh/sign/residual_size/delta4/delta13/delta_w/ema5/lag5/neg）→ 二元算子（ratio/diff/rank_minus/product）；
- 评估：dev 窗逐周 rank IC；fitness = |mean IC| + 0.3·|ICIR| − 0.1·turnover_proxy；coverage ≥ 0.9；pass 阈值 |IC|≥0.005 且 |ICIR|≥0.10；
- 预算：两种模式各评估 60 条表达式；
- 判定：evolution 的 top-5 平均 fitness − random 的 top-5 平均 fitness ≥ 0.005 且 pass_count 不低 → supported；否则 not_supported。

## 实现

- `auto_research/scripts/expr_search.py`：表达式 AST、随机生成/变异/交叉、dev 评估、A/B driver、独立 expression library 写入；
- `auto_research/expr_search/cache/dev_panel.parquet`：dev 窗面板缓存（本地数据）；
- 产物：`expr_search/expr_search_result.json`、`expr_search/results/*.json`、`expr_library/top_expressions_seed*.json`。

## 结果（pilot v1，2026-10-21）

- 首轮（budget 60，评估在全部 dev 周，无 holdout）：random top5 mean fitness 0.1402 vs evolution 0.1066 → **not_supported**；
- 但发现了设计缺陷：**进化分支有大量无效/重复子代，实际有效评估只有 37/60**（random 60/60），预算不对等；且 top5 含算子等价重复（rank/tanh 同 IC）；
- 处理：保留 pilot claim（`CLM-EXPR-SEARCH-001`），修正实现后进入 v2：
  - 进化分支补齐到 60 条有效评估（无效子代用随机表达式填充）；
  - top-k 去重（相同 mean_ic/icir 视为等价）；
  - 增加 **holdout 协议**：前 70% dev 周用于搜索（selection），后 30% dev 周用于最终比较（holdout），避免纯 in-sample 比较；
  - claim 改为 `CLM-EXPR-SEARCH-<seed>-HOLDOUT`。

## 结果（v2，holdout）

（运行后由脚本追加；负结果同样入账。）


### v2 seed 1 结果（holdout）

| 指标 | random | evolution |
|---|---|---|
| search 窗 top5 mean fitness | 0.150962 | 0.159107 |
| **holdout top5 mean fitness** | **0.110598** | **0.084138** |
| pass_count | 43 | 46 |

- in-sample Δ = 0.008145（进化略高），但 **holdout Δ = -0.02646** → **not_supported**；
- 解读：反馈驱动搜索在 search 窗略优、在 holdout 反而更差——典型的搜索过拟合；与"自动找到更好因子"的强主张不符；
- claim `CLM-EXPR-SEARCH-001-HOLDOUT` approved；pilot 缺陷记录在 `CLM-EXPR-SEARCH-001-PILOT-V2`。


### 3-seed 汇总（holdout 协议，最终）

| seed | in-sample Δ | holdout Δ | random holdout | evolution holdout | pass (rand/evo) |
|---|---|---|---|---|---|
| 1 | 0.008145 | -0.02646 | 0.110598 | 0.084138 | 43/46 |
| 2 | -0.005762 | 0.029185 | 0.096922 | 0.126107 | 44/42 |
| 3 | 0.008 | -0.021924 | 0.118423 | 0.096499 | 48/46 |

- 平均：in-sample Δ = 0.003461（弱正），**holdout Δ = -0.0064**（负）；仅 1/3 seed 的 holdout Δ ≥ +0.005，pass_count 不低者 1/3；
- **verdict: not_supported**（claim `CLM-EXPR-SEARCH-AGG-001` approved）；
- 结论：按当前设计，**反馈驱动的表达式搜索没有稳定优于随机搜索**；in-sample 的弱优势不能迁移到 holdout —— 与"自动找到更好因子"的强主张不符（当前证据为否）。

### 局限

- 预算 60/模式、3 代、3 seeds；holdout 只占 dev 后 30%；
- 表达式原语与语法有限（无时序回归、无截面回归残差、无组合）；
- 搜索目标（IC/ICIR/turnover 的组合）容易过拟合 search 窗；
- score 窗不参与，无 OOS 最终验证（历史一次性验证已用，不得再读）。


### 与现有面板特征 baseline 对比（dev holdout）

- baseline = 现有面板特征（raw）在 holdout 上的 top5 mean fitness = **0.146254**；
- 生成表达式（3 seeds 的 random/evolution 最好 holdout top5）= **0.126107**；
- Δ = **-0.020147** → verdict：**baseline_better_or_equal**（claim `CLM-EXPR-SEARCH-BASELINE-001` approved）。

**双重否定**：
1. 反馈进化 vs 随机：holdout 平均 Δ −0.0064（不优于随机）；
2. 生成表达式 vs 现成特征：最好也低于 baseline（−0.0201）。

当前系统还没有"找到更好因子"的证据；下一步转向**增量/残差目标**（不是 standalone IC，而是相对现有因子的增量信息），这是更合理的"更好"定义。

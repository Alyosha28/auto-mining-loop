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

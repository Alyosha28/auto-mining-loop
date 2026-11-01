# 目标 A 预注册计划：自动发现可超过强基线的因子

## 问题
在 dev 窗内，给定相同评估预算与相同先验，agentic 搜索能否稳定超过强基线（冷启动 random、evolution、同先验 random mutation、surrogate/BO）？

## 假设
- H1（warm start）：从既有因子库出发的变异优于冷启动 random（已有 13/13 证据）。
- H2（LLM 机制约束）：保持经济机制、只改 horizon/算子的 LLM 变异，优于同 parents 的随机变异。
- H3（QD）：MAP-Elites 质量多样性归档优于单一 top-k 选择。
- H4（surrogate）：用已评估样本拟合 fitness 代理，LLM 只在代理高分区域提案，可提升样本效率。

## 协议（预注册）
- 数据：仅本地 dev 窗 2017-01-01..2020-11-30；search/holdout 按 0.6/0.7/0.75 三个时间 split。
- seeds：7, 9, 10, 11, 12, 13, 17（7 个）。
- budget：24（主）、48（复核）。
- 基线：random、evolution、random mutation of library parents、Ridge surrogate + random。
- 系统臂：QD-Elites + LLM 机制约束变异（主）；消融：无 QD、无机制约束、无 KG。
- 指标：holdout top5 fitness（主）；IC/ICIR/turnover、稳定性、覆盖率（次）。
- 统计：paired delta 的 bootstrap 95% CI；符号检验；多假设 Holm 校正。
- 成功判据：主臂相对**最强基线**的配对 Δ 在 ≥2/3 splits 上 95% CI > 0，且 Holm 校正后显著；否则记为负结果。
- 停止规则：预算耗尽或连续 8 次评估无提升（stagnation）。
- 混杂控制：warm start 必须披露；同先验对照（LLM vs random mutation）单独报告；holdout 复用需在 claim 中注明。

## 产物
代码、逐 run 结果、claims、报告、复现命令；负结果同样入账。

# Benchmark 限制（v0.1）

1. **纯合成**：planted/weak/null 都是高斯线性信号，不能代表真实因子的非平稳、厚尾、行业结构；
2. **单一 BO 配置**：GP + AST 特征 + UCB(β=1.5)，没有树核/TPE/SMAC；BO 的结论不能推广到所有 BO；
3. **小 seeds**：默认 5 seeds；QD/BO 的 seed 方差在 n=5 时仍可能掩盖差异；
4. **指标单一**：holdout top-5 fitness；没有组合层/换手/容量/交易成本；
5. **无 LLM**：公开 benchmark 默认 LLM-free；LLM 版结果只在论文/真实数据脚本中报告，外部无法一键复现（依赖 API）；
6. **真实数据不在包内**：受数据边界约束，A 股 dev 窗结果不可随包分发；
7. **null 任务**：top-5 fitness 接近 0，但 naive `passed` 仍有 40–56%，说明"通过率"本身不是可靠指标。

## 使用建议
- 把本 benchmark 当作**相对比较**工具，不要当作绝对 IC 基准；
- 报告时必须同时给 seed 数、budget、split、以及是否 LLM-free；
- 新任务/新基线请写新 frozen 版本，并在 CHANGELOG 记录。

8. **regime 任务失败**：符号翻转的 regime 任务上所有方法（含 QD）都退化到 0 附近；本 benchmark 的静态表达式不包含自适应/状态切换机制。

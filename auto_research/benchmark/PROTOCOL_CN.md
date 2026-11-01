# Benchmark 协议（v0.1）

- 面板：250 symbols × 160 weekly dates × 40 features；
- split：search 70% / holdout 30%；
- budget：24 次评估（可选 48）；
- seeds：1–5（默认）；QD/BO 可选更多；
- 指标：holdout top-5 fitness 均值；`passed` 为 top-5 中通过搜索窗门槛的数量；
- 基线：random、evolution（seed+1000）、GP-UCB BO（AST 特征，UCB β=1.5）、QD-Elites（least-filled-cell 父代选择，无 LLM）；
- 所有方法共享同一 feature pool、预算、split；
- 输出：`results/bench_*.json`、`LEADERBOARD_*.md`；
- 版本：v0.1（2026-10-22），后续变更写入新版本文件，不覆盖。

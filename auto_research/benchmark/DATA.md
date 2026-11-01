# 数据说明

- 本 benchmark 只使用**合成面板**（`make_panel` 生成：250 symbols × 160 weeks × 40 特征），不含任何专有数据；
- 任务：planted（β=0.35）、weak（β=0.12）、null（无信号）；
- 真实 A 股 dev 窗的 QD/BO 对照**不在本目录**，只在论文与 `auto_research/agents/` 中报告，受数据边界约束；
- 可自由复制、修改、再分发（MIT）。

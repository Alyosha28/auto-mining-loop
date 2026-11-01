# Score 窗一次性最终验证协议（**已使用过；不得再读**）

> 状态：**ALREADY EXECUTED once（历史 RSI 最终验证）**
> 证据：`evo_rsif/archive/rsi_project_docs_20261021/SCOREWINDOW_VERIFICATION_CN.md`
> 原则：score 窗只读一次、零反馈进 loop；**本协议记录其已使用状态**，禁止任何新的 score 窗读取（包括本 paper-readiness 阶段）。

## 1. 已执行的最终验证（2026-10-21 09:00，历史）

- 窗口：2021-01-01 → 2023-12-31（最后交易日 2023-12-29）；
- 内容：E5/E5b/E5c/E5d 与 E6 多 seed 的 score 窗均值（IR / rank IC / 正年占比）；
- 结论：4 面板中无跨面板一致正增益（r23 evolved 更好、joint r1/fundamental base 更好、indicator 近乎相同）→ H5 "弱正、混合"；
- 该结果已写入 claim `CLM-20261021-0005`（`score_window_read=true`）。

## 2. 对 auto_research 新增工作的约束

- P1/P2/A1–A6 的所有新增实验均为 **dev 窗 + fair replay**；未新增 score 窗读取；
- paper 的 C1–C4/C6–C9 不依赖 score 窗；C5（H5 真实库增长）引用上述历史最终验证结果，并明确标注"仅一次"；
- 任何未来工作（包括论文修订）**不得**再次读取 score 窗；若确需，必须新建独立预注册并说明为何不污染已报告结果（默认禁止）。

## 3. 决策记录（2026-10-21，paper-readiness）

- G3 **完成**：score 窗一次性最终验证已在历史 RSI 阶段执行（证据在 archive），本阶段**不**再执行；
- 待办：无（候选集即历史 E5/E6 批次；已在 claim 0005 中记录）。

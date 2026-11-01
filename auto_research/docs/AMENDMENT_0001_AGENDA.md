# Amendment 0001：agenda 驱动的自主研究（2026-10-21）

- 背景：用户要求"设置 goal，你自己进行 auto research 决定"。
- 变更：新增 `auto_research/docs/AGENDA_CN.md` 作为研究议程的显式状态；agent 每轮自主选择 active item 推进。
- 边界不变：dev/embargo/score 窗口、冻结 verifier、append-only、score 窗零反馈、不改 evo_rsif 证据与主库。
- 记录：所有 agenda 变更追加到本文件（后续 amendment 追加），不覆盖历史。

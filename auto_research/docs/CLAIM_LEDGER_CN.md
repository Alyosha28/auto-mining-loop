# Claim Ledger 与 Review 规则（v0）

## 1. Claim schema（`ledger/claims.jsonl`，append-only）

```json
{
  "schema": "AUTO-RESEARCH-CLAIM-1",
  "claim_id": "CLM-20261021-0001",
  "project": "rsi_backfill",
  "type": "experimental_result | structural | methodological",
  "statement": "一句话结论（含数字/verdict；不含未验证的因果）",
  "evidence": [{"path": "evo_rsif/runs/.../chain_result.json", "sha256": "..."}],
  "run_refs": ["evo_rsif/runs/rsif_e1_s7"],
  "preregistration": "evo_rsif/PREREGISTRATION.json",
  "score_window_read": false,
  "supersedes": null,
  "created_at": "...",
  "status": "unverified"
}
```

规则：
- claim 一旦写入不可修改；更新结论 = 新 claim + `supersedes` 指向旧 claim；
- evidence 必须是 workspace 内已存在的文件（不引用目录；目录用 run_refs）；
- `score_window_read` 必须如实填写（是否读取过 2021–2023 数据）。

## 2. Review 记录（`ledger/reviews.jsonl`，append-only）

```json
{
  "schema": "AUTO-RESEARCH-REVIEW-1",
  "claim_id": "CLM-...",
  "reviewer": "rules-v0",
  "decision": "approve | reject",
  "checks": {"R1_evidence_exists": true, "...": true},
  "reasons": ["..."],
  "reviewed_at": "..."
}
```

## 3. rules-v0 检查项

| 检查 | 内容 | 失败处理 |
|---|---|---|
| **R1 evidence exists/hash** | 每个 evidence 路径存在且 sha256 匹配 | reject |
| **R2 statement specificity** | 陈述 ≥ 20 字符，且含数字/指标/verdict 关键词（支持/不成立/成立/mixed/not） | reject |
| **R3 evidence composition** | `experimental_result` 至少 2 条 evidence，且同时含文档（.md/.json）与数据（chain_result/step_record/summary/manifest/fair_result/admission/panel_table/scheduler_result） | reject |
| **R4 score-window leakage** | 若 evidence 含 `scores.parquet`，必须另有 dev 侧证据（chain_result/step_record/summary/admission）；`score_window_read=true` 的 claim 不得声称"loop 内优化" | reject |
| **R5 prereg reference** | `experimental_result` 必须给出存在的 `preregistration` 路径 | reject |
| **R6 overclaim guard** | 陈述含 RSI/autonomous research 时，必须同时含 bounded/not supported/no evidence/weak/limited | reject |
| **R7 evidence immutability** | evidence 不得使用 `.jsonl`/`.log` 等 append-only 日志（hash 不稳定），必须用不可变文件或快照 | reject |

reviewer 必须**独立于生成方**：v0 为规则集；v1 计划用跨模型 reviewer（ARIS 式）交叉审核，并记录模型身份与成本。

## 4. 命令

```bash
# 添加 claims（从 spec；已存在 claim_id 跳过）
.venv/bin/python auto_research/scripts/claim_ledger.py add --spec auto_research/claims/spec_rsi_backfill.json
# 审核所有未审核 claims
.venv/bin/python auto_research/scripts/review_claims.py run
# 查看/校验
.venv/bin/python auto_research/scripts/claim_ledger.py list
.venv/bin/python auto_research/scripts/claim_ledger.py verify
```


## 版本

- `rules-v0.1`：P2 harness 提案 reviewer（合法编辑/证据 claim/预期指标/score 窗）。
- `rules-v0.2`（当前）：R3 接受 panel_table/scheduler_result 作为数据证据；支持按 claim-id 定向重审。


## 证据快照约定（v0.4 实践）

- 可变的聚合产物（例如 `panel_table.json` 会被新面板追加）**不能直接作为 claim evidence**；
- 用法：先复制 `*_snapshot_<tag>.json`（不可变），claim 引用快照；
- claim 结论需要更新时：追加新 claim 并写 `supersedes`，`claim_ledger.py verify` 会跳过被取代 claim 的 hash 检查（仍保留复核记录）。
- 实例：`CLM-P2-SCHEDULER-001` → `CLM-P2-SCHEDULER-001-V2`（快照版）。

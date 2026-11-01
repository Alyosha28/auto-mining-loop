# P0 报告：claim ledger + rules-v0 reviewer（auto-research v0）

- 生成时间：2026-10-21T01:55:21.342807+00:00
- 回填 claims：8（首次 run 因一条 evidence 路径错误已写入 6 条；修正后重跑补齐 2 条，共 8 条，无重复）
- reviewer 决策：approve 8 / reject 0
- ledger：`auto_research/ledger/claims.jsonl`、`auto_research/ledger/reviews.jsonl`（append-only）

## Claims

| claim_id | type | verdict | statement |
|---|---|---|---|
| CLM-20261021-0001 | experimental_result | approve | 多轮顺序递归（sequential recursion）在 7 个 seed 上均改善 dev select utility（均值 +0.0236；固定 harness 恒 0.0354），plateau ≤4 轮，H1 bounded self-refinement 成立。 |
| CLM-20261021-0002 | experimental_result | approve | 扁平 lesson、结构化 lesson、LLM 自提议三种重测均未提升提案质量或 adopted utility（H2 不成立：memory 只改编辑顺序，LLM 与 deterministic 均 1/5 命中）。 |
| CLM-20261021-0003 | experimental_result | approve | evolved harness 在两个 OOD domain 优于 base（intraday 5 seeds 非负、均值 Δ+0.0213；fundamental Δ+0.0201），但单算子消融为负、候选集 Jaccard 0.33–0.66，效应非可加（H3 成立但为组合/预算重排效应）。 |
| CLM-20261021-0004 | experimental_result | approve | 退火编辑预算单独≈无正则（0.0635 vs 0.0632）；加入 exploration bonus 后修复 memory+退火的倒退（0.0486→0.0635），H4 正则需要配套探索奖励。 |
| CLM-20261021-0005 | experimental_result | approve | 公平回放（同一库快照）下 evolved 在 fundamental 与 r23 两面板多 seed 新增成员更多（各 5/5 seeds；均值 7.8 vs 5.0、4.0 vs 2.0），但 joint r1 为负、score 窗方向混合，H5 为弱正混合。 |
| CLM-20261021-0006 | structural | approve | evo 实验只写独立库（active v9、44 成员），主库 v59/465 零写入；registry 显示 2 个库，隔离成立。 |
| CLM-20261021-0007 | experimental_result | approve | 所有 evo probe 与 mining 批次 evaluable coverage=1.000，未触发 <0.96 STOP（P0 批、E5/E6/E8 批次均 coverage 1.0）。 |
| CLM-20261021-0008 | methodological | approve | 无证据支持 meta-level RSI；结论为 bounded self-refinement + weak meta evidence，不宣称 autonomous research（H2/H5 为负或弱正）。 |

## 被 reject 的原因

- 无

## 校验

- `python auto_research/scripts/claim_ledger.py list`
- `python auto_research/scripts/claim_ledger.py verify`（证据 sha256）
- `python auto_research/scripts/review_claims.py run --force`（重审）

## 下一步（P1）

1. 从 claims/experiments 自动构建 knowledge graph（panel/family/operator/claim 节点 + supports/contradicts/tested_on 边）；
2. typed retrieval（similar/contradictory/cross_domain/counterintuitive）接入 hypothesis generator；
3. hypothesis generator + experiment designer 产出第一条 P1 实验并自动回填 claim。

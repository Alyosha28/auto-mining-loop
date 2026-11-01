# Benchmark Changelog

## v0.1 — 2026-10-22
- 包元数据：`pyproject.toml` v0.1.0，依赖锁定 `requirements.txt`。
- 初始版本：3 synthetic tasks（planted/weak/null）× 5 seeds × budget 24；
- 基线：random、evolution（seed+1000）、GP-UCB BO、QD-Elites（无 LLM）；
- 指标：holdout top-5 fitness、top-5 passed 数；
- 冻结结果：`results/frozen/v0.1/`，SHA-256 `45b06db28712ea6e4aa0ecf046e7c0d3fd27998e0d471d83629f235c085595af`；
- CI：`ci.sh` 4/4（tests/quickstart/output checks/frozen hash）；
- 已知限制：QD 的 skeleton dedupe 在合成任务上从未触发；BO 仅 GP+AST 特征版本。

## 变更政策
- 已冻结版本不覆盖；新结果写入新版本目录；
- 协议/基线/指标变更必须在本文件记录；
- 论文引用必须绑定 frozen 版本与 SHA-256。

## v0.2-ood — 2026-10-22
- 新增非线性/异方差/regime 三类分布外任务；
- 冻结结果：`results/frozen/v0.2-ood/`；
- 结论：QD 在 nonlinear/hetero 上仍胜；regime 上所有方法退化（边界条件）。

## v0.1-repro — 2026-10-22
- 从零重跑 v0.1 全量配置，12/12 数值 delta=0；
- 冻结：`results/frozen/v0.1-repro/`。

## v0.3-regime-state — 2026-10-22
- 新增 `regime_state` 任务（带已知状态特征 f_state）；
- 冻结：`results/frozen/v0.3-regime-state/`；
- 结论：仅加状态特征不能恢复 regime 表现；BO 略升，QD 不变。

## v0.4-gate — 2026-10-22
- QD 加入 20% gate-biased mutation（仅 regime_state）；
- 冻结：`results/frozen/v0.4-gate/`；
- 结果：regime_state QD 0.0278（原 −0.0213），全 gate 0.1007，仍远低于手工 gate 0.999；
- 测试：新增 gate_search smoke test（共 6 个）。

## v0.5-b48 — 2026-10-22
- budget 48 合成 benchmark（3×5×4）；
- 冻结：`results/frozen/v0.5-b48/`；
- 结论：QD 在 planted/weak 仍最好，但对 BO 优势收窄（0.692 vs 0.688）。

## v0.6-force-gate (2026-10-23)

- added `--force-gate-init` to `bench.run` and `force_gate_init` to `core.run_qd_novelty` (default off);
- frozen run: tasks regime_state, regime_state2; seeds 1-3; budget 12; qd baseline; force gate on;
- result: regime_state -0.0268 -> +0.2033; regime_state2 +0.0079 -> +0.2304 (3 seeds, report as pilot).

## v0.7-force-gate-n48 (2026-10-23)

- runner end-to-end n=48 (b12, qd, seeds 2001-2048), force gate vs baseline;
- regime_state +0.0834 [0.0352,0.1338]; regime_state2 +0.0798 [0.0339,0.1303];
- frozen with reference baseline run; consistent with probe n=48.

## runner dose-response n=48 (2026-10-23)

- b6: +0.0534/+0.0542; b12: +0.0834/+0.0798; b24: +0.1000/+0.0974 (regime_state/regime_state2), all CI>0;
- monotone increase with budget; consistent with probe.

## regime_state3 (2026-10-24)

- new task: state = sign(cross-sectional mean |f00| - 0.8); beta=0.35*state;
- used for third-task force_gate_init replication (b12, n=48 two-stage).

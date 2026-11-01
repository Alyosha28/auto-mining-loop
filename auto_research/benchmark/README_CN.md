# Auto-Research Benchmark（离线、可复现）

## 安装
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
测试环境：Python 3.13.15 / numpy 2.5.3 / pandas 3.0.5 / scikit-learn 1.7.2。


目标：把因子搜索的"强基线 + 证据治理"打包成可公开运行的 benchmark，作为论文从筑基走向元婴的基础设施。

## 任务
- `planted`：β=0.35 的合成截面信号；
- `weak`：β=0.12；
- `null`：无信号（用于假发现率评估）。

## 基线
- `random`：随机表达式搜索；
- `evolution`：现有进化搜索（seed+1000，避免与 random 流重合）；
- `bo`：GP-UCB（AST 统计特征）；
- `qd`：QD-Elites + 算子骨架 novelty（**无 LLM**，离线可复现）。

## 运行
```bash
cd auto_research/benchmark
../../.venv/bin/python -m bench.run --tasks planted,weak,null --seeds 1,2,3,4,5 --budget 24 --baselines random,evolution,bo,qd --tag full
```
输出：`results/bench_*.json`、`results/LEADERBOARD_*.md`。

## 边界
- 纯合成数据，无专有数据；可公开；
- score 窗未读；不涉及主库/evo 库；
- LLM 基线（QD+LLM）不在此 benchmark 默认集中，避免外部依赖；可在论文中单独报告。

## 结果与版本

- [RESULTS_CN.md](RESULTS_CN.md)：全量结果与机制探针；
- `results/LEADERBOARD_v0.1.md`：版本化 leaderboard；
- `LICENSE` / `CITATION.cff` / `DATA.md` / `PROTOCOL_CN.md`。

## 快速开始

```bash
cd auto_research/benchmark
bash examples/quickstart.sh   # 1 task, 1 seed, budget 6, ~6s
```

## 测试

```bash
cd auto_research/benchmark
../../.venv/bin/python -m unittest discover -s tests -v
```

3 个测试：面板形状、QD budget/去重、CLI 输出字段；约 9 秒。

## 一键 CI

```bash
bash auto_research/benchmark/ci.sh   # tests + quickstart + output checks, ~14s
```


## 版本化与冻结

```bash
bash run_full.sh                                   # 全量配置
../../.venv/bin/python freeze_results.py --result results/bench_full_v0.1_*.json --version v0.1
bash ci.sh                                         # 含 frozen manifest 校验
```

## 版本 diff

```bash
../../.venv/bin/python diff_results.py v0.1 v0.1
```

## 变更记录

- [BENCHMARK_CHANGELOG.md](BENCHMARK_CHANGELOG.md)

## 扩展示例

```bash
cd auto_research/benchmark
../../.venv/bin/python examples/custom_baseline.py
```

## 限制

- [BENCHMARK_LIMITATIONS.md](BENCHMARK_LIMITATIONS.md)

## OOD 任务

```bash
bash run_full_ood.sh
```

## 最低复现路径

```bash
pip install -r requirements.txt
bash ci.sh          # 5 tests + quickstart + output + frozen hash, ~15s
bash run_full.sh    # full 3x5x24 benchmark
```


## 引用（BibTeX）

```bibtex
@misc{anonymous2026autoresearchbench,
  title  = {Auto-Research Benchmark: evidence-grounded factor search},
  author = {Anonymous Authors},
  year   = {2026},
  note   = {Version 0.1; synthetic data only; MIT license}
}
```


## 目录布局要求（重要）

本 benchmark **必须放在完整仓库内运行**：`bench/core.py` 复用 `auto_research/scripts/expr_search.py`，后者会加载 `skills/current/factor-evolution-harness2/scripts/evo_probe.py`。单独复制 `benchmark/` 目录会在 import 阶段失败；请 clone 整个仓库后在 `auto_research/benchmark/` 下运行。

## 可复现性

- [REPRODUCIBILITY.md](REPRODUCIBILITY.md)


## Regime / gate 实验

```bash
# 带状态特征的 regime 任务
../../.venv/bin/python -m bench.run --tasks regime_state --seeds 1,2,3,4,5 --budget 24 --baselines random,evolution,bo,qd --tag gate

# 全 gate 搜索（每次变异包成 product(f_state, ·)）
../../.venv/bin/python -m bench.gate_search
```
结果：原 QD −0.021 → 20% gate-biased +0.028 → 全 gate +0.101；手工 gated 表达式 0.999。

## 预计耗时

| 命令 | 耗时（本机） |
|---|---|
| `bash ci.sh` | ~15s |
| `bash examples/quickstart.sh` | ~6s |
| `bash run_full.sh` | ~8min |
| `bash run_full_ood.sh` | ~8min |
| `python -m bench.gate_search` | ~1min |

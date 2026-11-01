# Benchmark 使用与扩展指南

## 引用
- 引用论文 + `BENCHMARK_CHANGELOG.md` 中的 frozen 版本与 SHA-256；
- 不要引用 `results/bench_*.json` 的临时文件，引用 `results/frozen/<version>/`。

## 运行
```bash
cd auto_research/benchmark
bash ci.sh          # 4/4 校验，~15s
bash run_full.sh    # 全量 3×5×24
```

## 扩展任务
1. 在 `bench/core.py` 增加 `make_*_panel` 或复用 `synth_benchmark.make_panel`；
2. 在 `bench/run.py` 注册新 task；
3. 新结果写入新 frozen 版本，不覆盖 v0.1；
4. 在 CHANGELOG 记录协议/指标变化。

## 扩展基线
- 新基线必须实现 `run_*(panel, features, search_dates, seed, budget) -> list[dict]`，每行含 `node`/`formula`/`fitness`；
- 预算、split、feature pool 必须与现有基线一致；
- 结果必须报告 `passed_mean` 与 `score_window_read=false`。

## 边界
- 只用合成数据；真实 A 股数据不在 benchmark 内；
- 不读 score 窗；不写主库/evo 库。

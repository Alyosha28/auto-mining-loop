# 可复现性说明（benchmark v0.1 / v0.2-ood）

## 环境
- Python 3.13（3.11+ 应可）；
- 依赖锁定：`requirements.txt`（numpy 2.5.3 / pandas 3.0.5 / scikit-learn 1.7.2 / matplotlib 3.11.2）。

## 最小复现路径（仓库内）
```bash
git clone <repo> && cd <repo>
python -m venv .venv && source .venv/bin/activate
pip install -r auto_research/benchmark/requirements.txt
bash auto_research/benchmark/ci.sh
```
预期：
- 5 个测试通过；
- quickstart 生成 `results/bench_quickstart_*.json` 与 `LEADERBOARD_quickstart.md`；
- `CI_OK`；
- `FROZEN_OK 2`（若有冻结结果）。

## 全量
```bash
bash auto_research/benchmark/run_full.sh      # v0.1 合成 3×5
bash auto_research/benchmark/run_full_ood.sh  # v0.2-ood
```

## 预期输出字段
- `summary[task][baseline] = {n, mean, passed_mean, delta_vs_random, wins_vs_random}`；
- `score_window_read = false`；
- 冻结结果含 `MANIFEST.json`（sha256）。

## 常见失败
1. `ModuleNotFoundError: expr_search`：单独复制了 `benchmark/`；必须在完整仓库内运行（见 README“目录布局要求”）；
2. `no frozen manifest`：干净 clone 无 frozen 时 CI 会跳过（`FROZEN_SKIPPED`），不是失败；
3. sklearn `ConvergenceWarning`：GP 长度尺度撞界，属预期警告，不影响结果；
4. AppleDouble `._*` 文件导致 JSON 读取失败：macOS 挂载产物，删除即可。

## 版本
- v0.1：`results/frozen/v0.1/`，SHA-256 `45b06db2…`；
- v0.2-ood：`results/frozen/v0.2-ood/`，SHA-256 `d8232b3f…`。

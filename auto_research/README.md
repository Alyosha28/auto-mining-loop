# Auto-Research：诊断驱动的研究与 harness 进化

当前目标是让 agent 根据真实实验反馈改进研究过程。核心实现复用 harness² 的 Propose–Probe–Compose、atomic proposal、active branch、frozen champion 和 probe-only exploration，外层路线提供有界 `EXPLOIT / REPAIR / FORK / REFRAME / STOP` 选择。

完整架构见 [docs/DESIGN_CN.md](docs/DESIGN_CN.md)，论文论证与表达规则见 [docs/WRITING_IMPROVEMENT_PLAN.md](docs/WRITING_IMPROVEMENT_PLAN.md)。

## 当前证据

- V001 是诊断 continuation 的探索性开发面板，配对中位数为 `0.0`，证据状态为 `development-panel null`。
- V002 是累计记忆组件消融，memory 减 scalar 的中位数为 `+0.0012239560`，未满足预定一致性标准，证据状态为 `development-panel null`。
- V003 是完成的确认性开发面板，包含六个任务、两个开发划分、两次模型复现、24 个配对单元和 288 次模型调用。memory 减 random 与 memory 减 fixed 的中位数分别为 `+0.0035336384` 和 `+0.0109768722`，两项精确任务符号翻转检验均为 `p=0.03125`。memory 减 scalar 与 diagnostic 减 memory 的 Holm 校正 p 值均为 `1.0`。
- V001–V003 的冻结结果、派生表和恢复检查证据已打包到 [writing_runs/auto_research_harness_v003_20261031/evidence/](writing_runs/auto_research_harness_v003_20261031/evidence/)。

证据窗口为 2017-01-01 至 2020-11-30。dev-holdout、合成 benchmark、开发面板和独立 OOS 分开命名；当前没有 untouched 独立 OOS。

## 目录

| 路径 | 用途 |
|---|---|
| `agents/` | 历史 QD baseline、Agent 响应与本地研究 worker |
| `scripts/` | dev 表达式评价、研究配置构建和结果报告 |
| `config/` | V001–V003 的冻结研究协议 |
| `benchmark/` | 合成面板与 LLM-free 搜索回归 |
| `meta/` | 可复用的沙箱 patch/A-B runner |
| `docs/` | 当前架构、执行协议和写作规则 |
| `writing_runs/` | V003 论文、证据、图件与编译结果 |

实验 run、模型 cache、claims/reviewers 等本地工作目录不进入公开仓库。正式因子库与生产 score/admission 不属于本项目包。

## 验证

在仓库根目录创建 `.venv` 并安装 `requirements-dev.txt` 后：

```bash
.venv/bin/python -m pytest -q tests
bash auto_research/benchmark/ci.sh
```

论文检查入口见 [writing_runs/auto_research_harness_v003_20261031/README.md](writing_runs/auto_research_harness_v003_20261031/README.md)。`ci/guard.sh` 仅用于旧 claims/论文流程，不是当前探索的默认门禁。

## 数据边界

原始市场数据只使用用户另行提供的授权本地输入。公开仓库不包含原始行情、`Data/`、`data_processed/` 或完整实验 run。开发窗口之外的已暴露历史结果不得写成 final OOS，禁止向模型发送原始行情或封窗内容。

失败、null、撤回和无效提案必须保留。探索中的 coverage 只记录和诊断，跨候选比较使用共同可评估样本，不静默填补或删周。

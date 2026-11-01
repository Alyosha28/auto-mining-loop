# Auto Mining Loop

诊断驱动的 auto-research 与 research-harness evolution。仓库包含当前研究代码、harness²、V001–V003 配置、V003 论文源码与编译 PDF，以及论文复算所需的结果和派生表。

仓库：https://github.com/Alyosha28/auto-mining-loop

## 内容

- `auto_research/`：研究协议、诊断驱动的 proposer/route loop、合成 benchmark、研究文档和 V003 论文工作区。
- `skills/current/factor-evolution-harness2/`：Propose–Probe–Compose、atomic proposal、active branch、frozen champion、probe-only exploration 和共同支持终评。
- `skills/current/auto-research-paper-writing/`：项目特化论文写作规则。
- `research/`、`scripts/`：auto-research 实际调用的报价因子、数据修正、模型运行时和研究治理模块。
- `tests/`：研究回路与上述最小依赖的合成测试。

正式因子库、生产 score/admission、生产回测结果、前端和其他论文工作区均不在本仓库范围内。

## V003 开发面板

V003 包含六个任务、两个开发划分、两次独立模型调用复现、24 个配对单元和 288 次模型调用。主要结果：

- memory 减 random 的中位数为 `+0.0035336384`，六个任务中位数均为正，精确任务符号翻转 `p=0.03125`。
- memory 减 fixed 的中位数为 `+0.0109768722`，24 个单元均为正，精确任务符号翻转 `p=0.03125`。
- memory 减 scalar 的中位数为 `+0.0002895444`，Holm 校正 `p=1.0`。
- diagnostic 减 memory 的中位数为 `0.0`，Holm 校正 `p=1.0`。

证据窗口为 2017-01-01 至 2020-11-30。该研究没有独立真实数据样本外结果。论文入口为 [`auto_research/writing_runs/auto_research_harness_v003_20261031/README.md`](auto_research/writing_runs/auto_research_harness_v003_20261031/README.md)。

## 环境与验证

使用 Python 3.13：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
bash auto_research/benchmark/ci.sh
```

论文证据检查需要 Poppler 提供的 `pdfinfo` 与 `pdffonts`：

```bash
cd auto_research/writing_runs/auto_research_harness_v003_20261031
../../../.venv/bin/python scripts/build_workspace.py --check
../../../.venv/bin/python scripts/verify_manuscript_statistics.py
../../../.venv/bin/python scripts/assemble_manuscript.py --check
../../../.venv/bin/python figures/generate_figures.py --check
```

从论文工作区运行时，项目虚拟环境位于 `../../../.venv`。完整依赖、方法图重建和 LaTeX 编译要求见论文工作区 README。

## 数据边界

原始市场数据、`Data/`、`data_processed/`、实验 run、模型缓存、逐证券修正记录和内部会话不进入公开仓库。V003 的 `evidence/` 只包含论文所需的冻结结果、派生 CSV 和一份恢复检查 loop state。

新实验需要用户另行提供经授权的本地输入。公开克隆可以直接运行合成测试、统计复算和论文检查。

## 许可证

仓库整体保留所有权利，详见 `LICENSE`。`auto_research/benchmark`、字体和 LaTeX 组件保留各自许可证。

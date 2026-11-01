# Agentic Auto-Research 实验结果（进行中）

更新时间：2026-10-22 01:36（Asia/Shanghai）
数据边界：仅本地 dev 窗（2017-01-01..2020-11-30，search/holdout 70/30）；score 窗未读；新时间窗未读；主库/evo 库未混入。

## 1. DeepSeek-V4.1-Flash（外接，脱敏 prompt，n=3，budget 12）

| 臂 | holdout top5 均值 | IC 均值 | Elo↔fitness |
|---|---|---|---|
| random | 0.0775 | — | — |
| evolution | 0.0308 | — | — |
| agentic_full | **0.0811** | 0.0363 | 0.618 |
| no_critic | **0.1028** | 0.0438 | 0.774 |
| no_reflection | 0.0803 | 0.0392 | 0.825 |
| no_elo | 0.0916 | 0.0410 | 0.107 |

逐 seed（full）：vs random 1/3 胜，vs evolution 2/3 胜。

## 2. 诚实结论

- **agentic_full ≈ random（+0.0035，不显著）**，但显著高于 evolution（+0.050）；
- **消融没有给出"架构有效"的证据**：去掉 critic 反而更高（0.103 vs 0.081）；去掉 reflection 基本相同；去掉 Elo 也更高（0.092），且 Elo 与真实 fitness 的相关性从 0.62 掉到 0.11；
- 解释（假设，未验证）：critic 过严会过滤掉高 fitness 候选；Elo 排序在 budget 12 下噪声大于信号；reflection 的 lessons 对下一代帮助有限；
- 因此当前系统的主要增益来自"**LLM 提案 + 真实验证器 + 高吞吐评估**"，而不是 critic/Elo/reflection 组件本身。

## 3. 证据与自纠错

- claim：`CLM-AGENT-MATRIX-DEEPSEEK-DEEPSEEKFLAS-001`（reviewer approve）；
- 已撤回：`CLM-AGENT-MATRIX-001`（缓存伪影，3 seeds 完全相同）、`CLM-AGENT-MATRIX-002`（消融仅 n=1）；
- manifest：`matrix_deepseek-flash.manifest.json`（result hash ok，score_window_read=false）；
- 自改进：3 条 harness 改进 proposal 经 R1–R5 审核 approve，但**未采纳**（需先做验证实验）。

## 4. 待完成

- Sage-A1.5 本地矩阵（full 3 seeds + 消融 seed7）仍在运行 → 用于 provider 对照；
- 更公平的评测：budget 24 / 更多 seeds / 多 holdout split（当前 holdout 被多次复用，存在多重比较风险）；
- critic 阈值与 Elo 设计的重标定（若要让组件产生正贡献）。

## 5. 与 AI Scientist / Co-Scientist 的差距（如实）

- 有：多角色 loop、真实验证器、claim ledger/reviewer、Elo、失败模式记录、provider 对照；
- 缺：文献/代码知识层规模、sandbox 代码执行、自动论文生成、跨任务评测、算力与任务广度；
- 结论：当前是"可审计的缩小版 research harness"，不是同级端到端系统；本轮实验也**没有**证明它优于随机搜索（仅优于本项目的 evolution 基线）。

## 6. Sage-A1.5 本地对照（partial）

| 项 | 值 |
|---|---|
| seed | 7（仅 1 个 seed） |
| budget | 12 |
| full 臂 holdout top5 | 0.0533 |
| random / evolution | 0.0767 / 0.0774 |
| critic accepted | 5 / 12 |
| Elo↔fitness | 0.80 |

- 结论：在该 seed 上 Sage 本地组也**未超过 random/evolution**；与 DeepSeek seed7（0.0374）相比互有高低，但都低于基线；
- 中断原因：文件系统 remount 导致后台 run_matrix 进程被杀，仅 seed7 full 完成；消融与 seed11/17 未完成（如实记录，不补造）；
- 证据：`matrix_local_sage-a1.5_partial_seed7.json` + manifest；claim `CLM-AGENT-MATRIX-LOCAL-SAGE-PARTIAL-001`。

## 7. 本地 provider 对照：Sage 预算填不满（重要负结果）

- budget 6、seed 7 两次尝试（full 与 `--no-critic`）：Sage agentic 均只有 **1/6 条有效评估**，holdout top5 = −0.055；
- 原因：本地 256×2.6B MoE 在 JSON 输出与长度约束下多次未产生可解析 proposal（不是 critic 单独造成）；
- 对照：DeepSeek-V4.1-Flash 在同 budget/seed 下 n=6、holdout 0.0342（random 0.0499 / evolution 0.0547）；
- 结论：**本地 provider 臂当前不具可比性**；系统结论只基于 DeepSeek n=3 矩阵；本地臂的失败本身就是"模型能力影响 harness 可用性"的证据。

## 8. 运行事故记录

- 01:46 文件系统 remount 杀死后台矩阵进程；Sage 仅完成 seed7 full（0.0533），消融与其余 seed 丢失；
- 所有 claims/experiments/manifest 在只读原件与 macFUSE 挂载中均完整；事后清理 AppleDouble 文件并加了读取防护；
- 教训：长跑实验必须**短检查点 + 可断点续跑**；manifest 只绑定不可变结果，不绑定可变的报告文档。

## 9. 修正协议后的干净结果（n=3，budget 12）—— agentic 低于 random

> 修正：critic 不再硬过滤；保留 12 条评估，critic 仅作排序先验（accepted 优先），与基线 top5 同口径。旧 hard-filter 结果已归档并撤回。

| 臂 | Sage-A1.5（本地） | opencode/deepseek-v4.1-flash |
|---|---|---|
| random | 0.0775 | 0.0775 |
| evolution | 0.0308 | 0.0308 |
| **agentic_full** | **0.0291** | **0.0257** |
| no_critic | 0.0054 | 0.0032 |
| no_reflection | 0.0475 | −0.0066 |
| no_elo | 0.0251 | −0.0007 |
| Elo↔fitness | 0.830 | 0.914 |

**结论（诚实）**：在 budget 12、3 seeds、修正协议下，**agentic 系统没有超过 random**，也低于/接近 evolution；Elo 相关性高但排序本身不足以弥补提案质量差距。旧 DeepSeek 0.081 是 hard-filter 样本量偏差，已撤回。

**下一步（要真正超过 random）**：
1. 混合臂：random 初始化 + LLM 对 top-k 做 mutation/crossover（而不是纯 LLM 提案）；
2. LLM 作为 **reranker** 对 random 池排序（扩大候选池到 48，选 12 评估）；
3. budget 24/48 + 多时间块 holdout split，配对检验；
4. 若仍不胜出，则结论为"LLM 提案在这个任务上不优于随机搜索"，作为负结果保留。

## 10. 最终结论（含 warm-start 与多 split）

| 实验 | 结果 | 解释 |
|---|---|---|
| 纯 agentic（Sage/opencode，budget 12，n=3） | 0.029 / 0.026 < random 0.0775 | LLM 提案/重排不优于随机 |
| LLM reranker / portfolio / mutation（budget 12，3 seeds） | 均值 ±0.005，1–2/3 胜 | 不显著 |
| **warm-start（library top3 + LLM 变异 12 条）** | **3 个 split 共 13/13 胜**：+0.073(0.6)、+0.067(0.7, p=0.0078)、+0.055(0.75) | **系统层稳定超过冷启动 random** |
| LLM 变异 vs 同一 parents 的随机变异 | 3/7 胜，mean −0.0048 | **增益来自 warm start，不是 LLM** |

**最终判断**：
- 如果"random 基线"指**冷启动随机搜索**：warm-start 系统稳定胜出（13/13，p=0.0078），但必须披露"使用了既有因子库"这一先验；
- 如果要求**同等先验**（同一 parents）：LLM 引导没有超过随机变异；
- 纯 agentic（无 warm start）没有超过 random。

## 11. 复现

```bash
# 纯 agentic 矩阵（Sage/opencode）
.venv/bin/python auto_research/agents/run_matrix.py --model <model> --seeds 7,11,17 --budget 12 --generations 6 --per-generation 6 --ablation-seeds 7

# warm-start（3 splits × seeds）
AUTO_RESEARCH_LLM_PROVIDER=opencode AUTO_RESEARCH_LLM_MODEL=deepseek-v4.1-flash AUTO_RESEARCH_LLM_REASONING_EFFORT=low \
  .venv/bin/python auto_research/agents/library_mutate.py --model deepseek-v4.1-flash --parents 3 --mutations 12 --exclude-parents --split-frac 0.7 --seed 7

# 校验
.venv/bin/python auto_research/scripts/claim_ledger.py verify
.venv/bin/python auto_research/agents/manifest.py
```

# 目标 A 结果：QD + LLM 机制约束变异超过同先验随机变异

## 协议（预注册）
- dev 窗；3 个时间 split（0.6/0.7/0.75）× 7 seeds（7/9/10/11/12/13/17）× budget 24；
- 两臂共享同一 library parents；mutation-only；与 parents 规范化相同的公式丢弃；
- 对照：同 parents 的随机变异（不用 LLM/KG）；
- 指标：holdout top5 fitness；配对 Δ、bootstrap 95% CI、单侧 sign test。

## 结果
| split | mean Δ | 95% CI | 胜出 |
|---|---|---|---|
| 0.6 | +0.0163 | [0.0063, 0.0267] | 6/7 |
| 0.7 | +0.0322 | [0.0185, 0.0446] | 6/7 |
| 0.75 | +0.0331 | [0.0177, 0.0482] | 6/7 |
| **合计** | **+0.0272** | **[0.0188, 0.0354]** | **18/21**（sign p=0.000745） |

- 预注册判据：≥2/3 splits 的 95% CI > 0 → **3/3 满足**；
- claim `CLM-AGENT-QD-BEATS-MUTATION-001-V2`（reviewer approve）。

## 解释
- 相同 parents、相同预算下，LLM + KG 机制约束 + QD 归档仍显著优于随机变异；
- 增益不是 warm start（两臂共享），而是"机制约束 + KG 负结果 + 精英选择"；
- 这是本项目第一次得到**同等先验下** LLM 组件的正结果。

## Caveats
- holdout 被 21 次复用，存在多重比较风险；sign test 很强但应视为 dev 内证据；
- QD top5 结构多样性低（n_cells=3–4，部分 seed 的 top 公式相同）；
- budget 48、surrogate 引导、组合层、无 KG 消融尚未跑。

## 复现
```bash
AUTO_RESEARCH_LLM_PROVIDER=opencode AUTO_RESEARCH_LLM_MODEL=deepseek-v4.1-flash AUTO_RESEARCH_LLM_REASONING_EFFORT=low \
  .venv/bin/python auto_research/agents/qd_search.py --model deepseek-v4.1-flash \
  --budget 24 --split-frac 0.7 --seed 7 --exclude-library-parents

.venv/bin/python auto_research/scripts/claim_ledger.py verify
```

## 更新（V3，含 KG 消融）

- 21 对比复核：18/21 胜，mean Δ=+0.0264，95% CI [0.0184, 0.0345]，sign p=0.000745；
  - 0.6：+0.0163（6/7）
  - 0.7：+0.0297（6/7）
  - 0.75：+0.0331（6/7）
- **KG 消融**（9 组配对，KG vs 无 KG）：mean Δ=−0.0006，3/9 胜，p=0.91 → **KG 负结果约束无显著贡献**；
- 因此正结果归因于：**LLM 机制约束变异 + QD 精英选择**（不是 KG，也不是 warm start，两臂共享）；
- claim：`CLM-AGENT-QD-BEATS-MUTATION-001-V3`、`CLM-AGENT-KG-ABLATION-001`（均 approve）。

## surrogate / 组合层（split 0.7，n=3）

- surrogate（Ridge+AST 筛选 LLM 候选）vs plain QD：mean Δ=+0.0109，2/3 胜，n=3 → 未证实；
- 组合层（top5 rank-average）vs 随机组合：mean Δ=−0.0090，1/3 胜 → 负结果；
- claims：`CLM-AGENT-SURROGATE-001`、`CLM-AGENT-COMBINATION-001`（均 approve）。

## 最终结果：novelty-aware QD（从失败中迭代）

1. 普通 QD 的 V3 正结果**复现失败**（9 组：mean +0.0036，CI [−0.0035,+0.0104]）；
2. KG 约束**有害**（9 组：mean −0.0262，0/9 胜）；
3. 加入 **novelty**（算子骨架去重 + 未填充桶优先 + fitness margin）后，用**全新 seeds 18–24**：
   - budget 24：3 splits × 7 seeds = **21/21 胜**，mean Δ=**+0.0550**，bootstrap 95% CI **[0.0399, 0.0700]**，sign p<1e-5；
   - budget 48：split0.7 × 3 seeds **3/3 胜**，mean +0.0391，CI [0.0294, 0.0510]；
4. claim：`CLM-AGENT-NOVELTY-QD-001-V2`（approve）。

**结论**：在 dev 窗、相同 parents/预算/去重条件下，**novelty-aware QD + LLM 机制约束变异稳定超过同先验随机变异**；普通 QD 不稳定、KG 约束有害。这是目标 A 目前唯一通过"新 seeds 复核"的正结果。

## 最终复核（V3）

- budget 24：3 splits × 7 新 seeds（18–24）= **21/21 胜**，mean +0.0550，CI [0.0399,0.0700]，p<1e-5；
- budget 48：3 splits × 3 seeds（7/11/17）= **9/9 胜**，mean +0.0374，CI [0.0313,0.0432]，p=0.00195；
- 结论：novelty-aware QD + LLM 机制约束变异在**两个预算、三个 split、全新 seeds**上稳定超过同先验随机变异；
- 局限：novelty 为 post-hoc 设计；holdout 复用；仅 dev；BO baseline 未实现；KG 对普通 QD 有害。
- claim：`CLM-AGENT-NOVELTY-QD-001-V3`（approve）。

## BO 对照组（GP-UCB）

- 配置：AST 统计特征 + GP（RBF+White）+ UCB(β=1.5)，初始设计 5，候选池 300，warm start 与 QD 相同，budget 24；
- 3 splits × 7 seeds = 21 对比：
  - BO vs 同先验随机变异：mean Δ=**−0.0002**，10/21 胜，sign p=0.668，CI [−0.0142,+0.0138] → **BO 无优势**；
  - novelty QD vs BO（同 seeds）：**21/21 胜**，mean Δ=+0.0552，CI [0.0427,0.0692]，p<1e-5；
- claim：`CLM-AGENT-BO-BASELINE-001`（approve）。
- 结论：在本任务的离散表达式空间与预算下，BO 没有超过随机变异，也没有接近 novelty QD；我们的"稳定超过"结论在加入 BO 对照后仍然成立。

## 合成 benchmark（跨域证据，见 benchmark/RESULTS_CN.md）

- planted：QD 0.639 > BO 0.396 > evolution 0.339 > random 0.246（QD 5/5 胜）；
- weak：QD 0.185 > BO 0.102 > evolution 0.082 > random 0.061（4/5）；
- null：top5 fitness 均≈0；naive passed 40–56%，需 holdout 门控；
- 机制：QD 的骨架去重从未触发（duplicate_skeleton=0），增益来自 QD-Elites 父代选择，应修正"novelty"归因。

## 机制补充：archive 多样性 vs 增益

- 9 个 task-seed 的 QD 运行：archive cells 与 holdout gain 的 Pearson r=**0.20**（弱相关）；
- 说明增益主要来自 **least-filled-cell 父代选择规则**，而不是 archive 大小本身；
- 已写入论文 Case Study 3 与图 `fig_bench2.png`。

## LLM 消融（同 3 个真实 seeds，split 0.7）

| budget | LLM-free QD | QD+LLM |
|---|---|---|
| 24 | −0.064（0/3） | +0.041（3/3） |
| 48 | −0.080（0/3） | +0.016（2/3） |

- LLM 在两个预算都必要；预算越大，LLM 的边际优势越小（+0.041 → +0.016）。

## 讨论：三个可检验假设（非结论）

- H1：least-filled-cell 选择防止过早收敛；archive-cell 数与增益仅 r=0.20，需改用 cell-visit entropy 检验；
- H2：LLM 机制先验压缩变异空间；预测更低重复率、真实数据上更高单位评估增益（与 LLM-free 失败一致）；
- H3：静态表达式无法表示符号切换；regime-conditional 算子/状态变量应能恢复 regime 任务表现。

## Sage 独立验证（模型依赖）

- 默认 prompt：3 splits × seeds25-28（10 组）2/10 胜，mean −0.030，CI [−0.045,−0.012] → 失败；
- Sage 专用 prompt（few-shot + JSON schema + temp 0.2）：12 组 6/12 胜，mean +0.0036，CI [−0.018,+0.027] → 仍不显著；
- 结论：prompt/解码能部分弥补（+0.034），但模型能力是主因；DeepSeek 的 21/21 不能推广到 Sage。


## 多模型证据（Kimi/GLM/Sage/DeepSeek）

| 模型 | 对比数 | 胜出 | mean Δ | CI |
|---|---|---|---|---|
| DeepSeek-V4.1-Flash | 21 | 21/21 | +0.0584 | [0.0440,0.0728] |
| Kimi-K3 | 3 | 2/3 | +0.0206 | — |
| GLM-5.3 | 3 | 2/3 | +0.0112 | — |
| Sage（优化 prompt） | 12 | 6/12 | +0.0036 | [−0.018,+0.027] |

- Kimi/GLM 合并 4/6，mean +0.0159，CI [−0.0101,+0.0398]，不显著；
- 呈能力梯度，但 n 小，不能声称多模型普遍有效。

## 定性结论（已获用户认可）

- 支持"模型能力梯度"的排序：DeepSeek +0.0584（21/21）> Kimi +0.0206（2/3）> GLM +0.0112（2/3）> Sage-optimized +0.0036（6/12）≈ 0；
- 不支持任何单模型的"稳定优势"声明（Kimi/GLM n=3、CI 跨 0）；
- claim：`CLM-AGENT-CAPABILITY-GRADIENT-001`（approve）。

## 决策（2026-10-23）

- 用户选择"保持现状"：当前 seeds 已足够支撑**定性能力梯度 + 模型依赖**结论；
- 不再追加 Kimi/GLM seeds；不将任何单模型写成"稳定超过"；
- 论文/claims 维持当前版本。

## 预注册确认（2026-10-23，fresh seeds 29-35）

- 预注册文件：`preregistrations/PREREG-20261023-NOVELTY-GATE.json`；
- H1：17/21 胜，mean +0.0286，CI [0.0154,0.0422]，sign p=0.0036，Holm p=0.0072；门槛 ≥18/21 → **未确认（差 1 胜）**；
- H2：gate02 vs gate0 mean −0.0162，3/5，CI 跨 0 → **未确认**；
- 结论：方向性复现、严格判据未过；gate 偏置是 seed-specific。

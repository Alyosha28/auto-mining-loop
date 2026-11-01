# 自动寻找更好因子：最终报告

日期：2026-10-21（goal-7bb19abe，expression search）
范围：dev 窗（2017-01-01..2020-11-30，embargo 2020-12）；score 窗零读取；主库零写入；独立 expression 库。

## 0. TL;DR

**四个范式中，前三个（standalone / 残差增量 / 稳定性正则化）均为负；第四个（组合搜索）在 dev holdout 上稳定为正。**

- 组合因子（2–5 个现有特征 rank 平均）的 holdout top5 fitness **0.1619 vs 最佳单因子 0.1334**（6 seeds，Δ **+0.0292**，6/6 positive）；
- 换时间切分仍为正：holdout 2020 Δ +0.0293（3/3）、holdout 2019–2020 Δ +0.0374（3/3）；
- 反馈进化 vs 随机：**9/12 seed-split 上更高**（弱-中等增量，非碾压）；
- **结论**：系统能自动找到"比现有单因子更好的**复合因子**"（dev holdout，可复现），但**没有**找到超过现有特征的**新单表达式**；也没有 OOS/score 窗验证（score 窗已在历史阶段一次性使用，不得再读）。

## 1. 系统设计

- **dev 面板缓存**：local_quote_library（33 特征 + 标签） + price_volume 采样 pv_*（56）→ dev 窗缓存；
- **表达式搜索**：AST（leaf/unary/binary，13 个一元算子 + 4 个二元算子），随机生成 + 变异/交叉 + 反馈选择；
- **组合搜索**：对现有特征做 rank-pct 后等权平均的 2–5 元子集，进化（加/删/换）vs 随机子集；
- **评估**：逐周 rank IC / ICIR / turnover_proxy / coverage；fitness = |IC| + 0.3·|ICIR| − 0.1·turnover；
- **A/B 协议**：search 窗选择（前 70% dev），holdout（后 30% dev）比较；同预算 60 条；
- **证据治理**：claim ledger + reviewer（R1–R9）+ 证据快照；负结果全部入账。

## 2. 四个范式的结果

| 范式 | 目标 | evolution vs random | vs 现有特征 baseline | verdict |
|---|---|---|---|---|
| v2 standalone | search 窗 IC/ICIR/turnover | holdout Δ −0.0064（1/3 正） | 0.126 < 0.146 | not_supported |
| v3 残差增量 | 解释 Ridge 残差 | 3/3 略高于 random（+0.0032 均值） | Δ −0.0011（≈0） | not_supported |
| v3b 稳定性 | 跨年 IC 一致性 | evo 0.1067 ≈ random 0.1056 | Δ −0.0278 | not_supported |
| **v4 组合** | 现有特征 rank 组合 | **9/12 高于 random** | **Δ +0.0292 ~ +0.0374（12/12 正）** | **supported（robust）** |

组合细节（robustness，3 切分）：
- frac 70/30（6 seeds）：evo 0.1619 / random 0.1512 / baseline 0.1334，Δ +0.0292，6/6 正；
- holdout 2020（3 seeds）：0.1473 / 0.1420 / 0.1233，Δ +0.0293，3/3 正；
- holdout 2019–2020（3 seeds）：0.1968 / 0.1800 / 0.1622，Δ +0.0374，3/3 正。

## 3. 解释：为什么单表达式失败、组合成功

- 单表达式搜索在 73 个特征 × 语法空间里搜索，selection 信号弱、易过拟合 search 窗；holdout 甚至低于现有特征；
- 残差目标下所有因子（含现有特征）增量 IC ≈ 0，说明 dev 窗残差预测性极低；
- 组合搜索的增益主要来自**降噪/降换手**（random 组合也超过单因子），进化选择再提供小幅增量（9/12）；
- 这与因子组合文献一致：弱信号因子的等权组合可提升 IR/降低换手。

## 4. 限定与威胁（诚实）

- 仅 **dev holdout**（2019-09..2020-11 等），无 OOS/score 窗验证（score 窗禁止再读）；
- '更好' = 该 dev 窗复合 fitness，不是投资表现；
- 多重比较：4 个范式、12+ 次运行，仅组合为正；应视为**探索性**，需独立数据/未来 campaign 复现；
- evolution 相对 random 的增量弱（9/12，幅度小）；
- baseline = 最佳单因子 top5（按 search fitness 选择后比较 holdout），未做更复杂 ML 基线；
- 未做组合权重优化（等权）与行业/市值中性化。

## 4.5 新时间窗一次性 OOS 验证（2024-01-01..2025-09-19）

用户授权新时间窗后，**在读取前冻结** dev 组合候选（`oos_2024/FROZEN_CANDIDATES.json`，sha256 校验），研究用一次性读取：

| 组 | top5 mean fitness |
|---|---|
| 冻结 dev 组合候选 | **0.1575** |
| 冻结随机组合控件 | 0.1219 |
| 冻结单因子控件 | 0.1371 |

- Δ（组合 − 单因子）= **+0.0203** → **supported**（claim `CLM-EXPR-OOS-2024-001-V2`；含协议偏差披露：验证命令误触发一次重读，结果确定性一致、无调参/反馈）；
- 未混入主库/evo 库（v59/465、v9/44 均未加载/写入）；score 窗未读；
- 详见 `docs/OOS_2024_2025_REPORT_CN.md`。

**修正后的结论**：组合搜索的正结果在独立时间窗上得到确认（dev 冻结 → 2024–2025 一次性验证）；但仍限于 IC/ICIR/turnover 复合 fitness，不是投资表现。

## 5. 失败台账

| 项目 | 结果 | 处理 |
|---|---|---|
| pilot v1（表达式搜索） | 进化有效评估 37/60（预算不对等）+ 算子等价重复 | 修正：补齐预算、top-k 去重、holdout 协议 |
| v2 standalone | not_supported（Δ −0.0064） | claim + 文档入账 |
| v3 残差增量 | not_supported（Δ −0.0011，全部 ≈0） | claim 入账 |
| v3b 稳定性 | not_supported（Δ −0.0278） | claim 入账 |
| 证据 hash 变动 | 多次（结果 json 被覆盖） | 快照 + superseding claim（verify 跳过历史） |
| reviewer 规则缺口 | 不认 incremental/stability/combo 数据文件 | rules v0.7–v0.9 逐次修复 |

## 6. 库状态

- 主库：v59 / 465，**零写入**；
- evo 独立库：v9 / 44，未改；
- expression 库：`auto_research/expr_library/`（top_expressions_*.json、top_combos.json），仅研究记录，未入主库；
- score 窗：历史一次性验证已用（claim 0005），本 goal 未读。

## 7. 复现命令

```bash
# dev 面板缓存
.venv/bin/python auto_research/scripts/expr_search.py --seed 1 --budget 60 --generations 3 --claim
# 残差增量
.venv/bin/python auto_research/scripts/expr_incremental.py --seed 1 --budget 60 --generations 3 --claim
# 稳定性
.venv/bin/python auto_research/scripts/expr_stability.py --seed 1 --budget 60 --generations 3 --claim
# 组合（含时间切分）
.venv/bin/python auto_research/scripts/expr_combo.py --seed 1 --budget 60 --generations 3 --claim
.venv/bin/python auto_research/scripts/expr_combo.py --seed 1 --budget 60 --generations 3 --holdout-start 2020-01-01 --claim
# 聚合与校验
.venv/bin/python auto_research/scripts/aggregate_combo.py
.venv/bin/python auto_research/scripts/aggregate_combo_robustness.py
.venv/bin/python auto_research/scripts/claim_ledger.py verify
```

## 8. 回答：这个系统是否真的找到了更好因子？

- **对"新单因子表达式"：没有。** 三个范式（standalone/残差/稳定性）都未超过现有特征；
- **对"复合因子"：有（dev holdout，稳健）。** 组合搜索找到的复合因子在 12/12 次运行中超过最佳单因子（+0.029 ~ +0.037 fitness），进化比随机在 9/12 次中更好；
- 因此严格表述是：**系统能自动找到更好的复合因子，但不能自动发明更好的新单因子；且证据仅限 dev 窗，需独立验证。**

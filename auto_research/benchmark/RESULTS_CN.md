# Benchmark 结果（v0，budget 24，seeds 1–5）

## 主表（holdout top5 mean）

| task | random | evolution | BO | QD-novelty |
|---|---|---|---|---|
| planted | 0.246 | 0.339 | 0.396 | **0.639**（5/5） |
| weak | 0.061 | 0.082 | 0.102 | **0.185**（4/5） |
| null | −0.016 | −0.018 | −0.011 | −0.019 |

- QD-novelty 在 planted/weak 上领先，且**不使用 LLM**；
- null 上所有方法的 top5 fitness 都接近 0（没有假发现意义上的高 fitness），但 naive `passed` 仍有 40–56%（搜索窗内过拟合）。

## 声明率（top5 中 `passed` 数，均值）

| task | random | evolution | BO | QD |
|---|---|---|---|---|
| planted | 4.2 | 3.8 | 4.2 | 5.0 |
| weak | 4.0 | 4.6 | 4.0 | 4.4 |
| null | 2.4 | 2.8 | 2.4 | 2.0 |

- null 任务上 naive 通过率 40–56% → 必须用 holdout 门控（与 synth_stats 的 strict 0/20 结论一致）；
- QD 在 null 上没有更高的假通过率（2.0/5）。

## 机制探针（QD）

- 24 次评估中：proposed 38–60，duplicate_formula 14–36，**duplicate_skeleton = 0**，unique_skeletons=24；
- 也就是说：**骨架去重（novelty dedupe）在合成任务上从未触发**；QD 的增益来自 **archive/least-filled-cell 的父代选择**，不是骨架去重；
- 这修正了我们在真实数据上对 "novelty" 的命名与归因：应称为 **QD-Elites 父代选择**，而不是 novelty 去重。

## 跨域对照

- 合成数据：BO 强于 random（planted +0.15），QD 更强（+0.39）；
- 真实数据：BO ≈ random mutation（10/21），QD+LLM 只 +0.055；
- 结论：任务结构决定代理模型是否有效；真实因子任务更难被平滑代理捕获。

## 复现
```bash
cd auto_research/benchmark
../../.venv/bin/python -m bench.run --tasks planted,weak,null --seeds 1,2,3,4,5 --budget 24 --baselines random,evolution,bo,qd --tag decl
../../.venv/bin/python -m bench.mechanism
```

## 真实数据补充：LLM-free QD

- 真实 dev（split0.7, budget24, seeds 18-20）：LLM-free QD vs 随机变异 **0/3 胜**（−0.158/−0.009/−0.026）；
- 对照：LLM 版 QD 同 seeds 21/21 胜；
- 结论：合成任务上结构搜索够用；真实因子上 LLM 机制约束变异是必要的。

- budget48 扩展：LLM-free QD 仍 0/3（−0.153/−0.060/−0.028），QD+LLM 2/3（+0.021/−0.003/+0.029）；48 时 LLM 优势收窄到约 +0.016。

## 分布外任务族（v0.2-ood，5 seeds，budget 24）

| task | random | evolution | BO | QD |
|---|---|---|---|---|
| nonlinear（tanh 信号） | 0.117 | 0.153 | 0.221 | **0.298**（4/5） |
| hetero（异方差噪声） | 0.096 | 0.117 | 0.165 | **0.279**（5/5） |
| regime（符号翻转） | −0.020 | −0.021 | −0.044 | −0.021（2/5） |

- QD 在非线性/异方差任务上仍最强；
- **regime（β 每周翻转）上所有方法都退化到 0 附近**——静态表达式无法适应符号切换，这是方法的边界条件；
- 冻结：`results/frozen/v0.2-ood/`。

- 机制补充：cell-visit entropy 与 holdout gain 的 Pearson r=0.18（n=9），与 archive-cell 数（r=0.20）一样弱；多样性指标本身不能解释增益。

## H3 检验：regime_state（v0.3）

- 加入已知状态特征 f_state（周奇偶 ±1）后：
  - QD：−0.021（与 regime 相同，未恢复）；
  - BO：−0.044 → +0.0003（略升，仍远低）；
  - random/evolution 仍≈−0.02；
- 结论：仅提供状态特征不足以恢复；需要显式 regime-conditional 算子或搜索偏置。

- H3 修复性检验：手工 gated 表达式 product(f_state, f00/f01) holdout IC=0.135/0.155、fitness=0.72/1.00，远高于 QD 的 −0.021 → 算子存在，但搜索找不到；需要搜索偏置/显式算子。

## budget 48 合成（v0.5-b48）

| task | random | evolution | BO | QD |
|---|---|---|---|---|
| planted | 0.531 | 0.578 | 0.688 | **0.692**（5/5） |
| weak | 0.161 | 0.137 | 0.160 | **0.236**（4/5） |
| null | 0.036 | 0.008 | 0.006 | 0.005 |

- QD 在 planted 仅 +0.004 领先 BO；weak 仍明显领先；null 上 QD 最低。

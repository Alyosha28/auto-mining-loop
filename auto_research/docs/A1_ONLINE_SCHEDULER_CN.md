# A1：随机化在线调度（UCB vs 固定轮询）

- 设计：4 面板 × 6 轮 × 2 seeds × {round_robin, ucb1}；max_candidates=3（关键：须小于各面板 passing 数，否则随机化惰性）；奖励 = fair-replay added/1000（v2 快照）；预算 150.0 min，实际 108.0 min。

## 每 seed 结果

| seed | policy | rounds | total reward/1000 | mean |
|---|---|---|---|---|
| 7 | round_robin | 6 | 333.33 | 55.55 |
| 11 | round_robin | 6 | 555.55 | 92.59 |
| 7 | ucb1 | 6 | 444.44 | 74.07 |
| 11 | ucb1 | 6 | 555.55 | 92.59 |

## 汇总

- round_robin 平均累计 reward/1000 = **444.44**
- ucb1 平均累计 reward/1000 = **500.00**
- verdict：**supported**（UCB 更高，+55.56/12 轮；seed7 上 UCB 444.44 > RR 333.33，seed11 持平 555.55）

## 关键修正（方法学）

- 首版用 max_candidates=40 时，所有面板 passing 候选（2–8）都小于 cap，`--candidate-seed` 不改变被评估集合，随机化**惰性**；已保留部分结果并重启为 max_candidates=3。
- 本结果依赖小 cap（3）；不能直接外推到生产 cap（40+），因为生产 cap 下随机化同样惰性。若要在生产规模测试在线调度，需要随机化 cap/预算分配本身，而不是候选顺序。

## 限制

- 2 seeds × 6 轮，样本小；reward 取值高度离散（0/111.11/333.33），均值差异由少数轮次驱动；
- UCB 的初始 4 轮强制探索与 RR 顺序相同（min-count 选择），只有后 2 轮体现利用差异；
- 结论应读作"在该设置下 UCB 不劣且略优"，不能外推为通用在线调度收益。


## 追加：seed 19 复现（A1b）

- seed 19：round_robin 444.44 vs ucb1 333.33 → **not_supported**（与 seed 7 方向相反）；
- 三 seed 汇总：round_robin 平均 **444.44** vs ucb1 **416.66** → **无差异（平手）**，原"supported"结论**不稳健**；
- claim `CLM-A1-ONLINE-SCHEDULER-SEED19`（approved）；汇总 claim `CLM-A1-ONLINE-SCHEDULER-AGG-001`。

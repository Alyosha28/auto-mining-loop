# 论文突破元婴计划（元婴 = 领域标准工具/benchmark）

## 元婴判据
1. **独立验证**：至少一个未参与方法设计的任务/数据上复现正结果；
2. **公开 benchmark**：他人可一键运行，包含强基线（random/evolution/BO/QD）与协议；
3. **机制解释**：说清 novelty 为什么有效（多样性-过拟合权衡），有可检验预测；
4. **跨域证据**：至少一个非 A 股任务（合成 planted/weak/null 或公开数据）；
5. **可复用产物**：代码、leaderboard、复现命令、负结果记录。

## 当前进度
- [x] benchmark 骨架：`auto_research/benchmark/`（合成任务 + 4 基线 + leaderboard）；
- [x] 真实数据：novelty QD 21/21（budget24）、9/9（budget48）超同先验随机变异；
- [x] BO 对照：真实数据 BO≈随机变异（10/21）；合成数据 BO 强于 random（初步）；
- [ ] 全量合成 benchmark（3 tasks × 5 seeds）；
- [ ] novelty 机制分析（去重率、多样性、holdout gap）；
- [ ] 论文整合（QD/BO/负结果/benchmark）；
- [ ] 独立窗口验证（需用户授权，当前禁止）。

## 下一步
1. 修 evolution/random 流重合，跑全量合成 benchmark；
2. 写机制分析：novelty 去重率 vs holdout 增益；
3. 更新论文方法/实验/附录；
4. 生成公开 benchmark 的 leaderboard 与 README。

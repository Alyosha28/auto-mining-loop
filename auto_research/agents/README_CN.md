# Agentic Auto-Research：当前组件

当前工作指引是 [../docs/DESIGN_CN.md](../docs/DESIGN_CN.md)。目标是诊断驱动的研究与 harness 进化，不是恢复旧的固定模型矩阵或大型预注册流程。

主底座是父项目的 dev 候选生成/评价和 `skills/current/factor-evolution-harness2`，不是本目录的小 QD。生产 score/admission 不进入 loop；不直接调用会先读全窗的生产 loader/CLI。旧 QD 只保留作历史对照。

**不要繁琐的证明链与审计。** 探索 run 保留 config、result、必要 trace 和恢复命令；失败、null 与无效提案不删除，不把 claim/reviewer/full guard 作为默认前置条件。

## 可复用组件

| 文件 | 当前定位 |
|---|---|
| `qd_search.py` | 既有 QD baseline/worker；固定 grammar 的表达式搜索，不是完整研究闭环 |
| `agent_qd_driver.py` | 按 run 衔接 Agent 原始响应与本地 QD；支持专用 cache 恢复 |
| `llm.py`、`models.py` | 旧模型客户端与缓存；具体路由以 run protocol 为准 |
| `expr_grammar.py` | 既有表达式解析与校验；不等于研究空间的上限 |
| `orchestrator.py`、`roles.py`、`compare.py` | 早期 proposer/critic/reflection 实验组件，复用前需重新核对 |
| `self_improve.py` | 早期 reviewer-gated 改进，不是当前默认采纳路径 |

R2 主要复用 `skills/current/factor-evolution-harness2` 的两阶段 proposer 和 Propose–Probe–Compose；`meta/meta_loop.py` 仅作通用文件 patch 的可选工具。诊断反馈对照、route history、连续 loop 和共同样本终评接口已经实现；真实路线自治与 GEPA 研究对照尚未运行。

## 模型路由

- 不再默认“只能使用本地 Sage”，也不等待 Sage 就绪才开始研究。
- 2026-10-25 的直接 OpenCode DeepSeek API 检查返回 `api_key_expired`；这只能说明该 key 失效，不能据此宣称 DeepSeek 整体不可用。
- Kimi Agent 通道 `opencode-go/deepseek-v4.1-flash` 已完成真实生成。使用 Agent 时应保留原始响应、实际模型和请求绑定；不得把它描述成直接 API 调用。
- 不静默更换模型或 provider。外部模型推理不等于外部市场取数；不得发送原始行情或封窗内容。
- 旧 `LIVE_QD_A5_20261024` Sage 任务是暂停的不完整实验，没有 primary analysis，不作为默认恢复路径。

## 当前真实结果

`DEEPSEEK_QD_S11_COVLOG_001`：seed=11、budget=12，QD dev-holdout 0.131398，random 0.131761，random-mutation 0.088618。top-5 有 3 个 library；单 seed 不支持普遍优势或整体无效。coverage 在该探索 run 中只记录、不作 STOP。

完整产物：`runs/DEEPSEEK_QD_S11_COVLOG_001/completion.json`。旧 `RESULTS_CN.md`、`A_RESULTS_CN.md` 和方法论文档是历史参考，不作为当前模型状态或新实验指令。

## 默认验证

运行与改动相关的测试和真实小调用；修改 benchmark 时运行 `bash auto_research/benchmark/ci.sh`。旧 `ci/guard.sh` 只用于 legacy claims/论文流程，不再作为每次探索的默认门禁。

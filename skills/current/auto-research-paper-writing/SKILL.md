---
name: auto-research-paper-writing
description: 为 auto_research 的研究运行准备、起草、重组或审阅论文内容。任务涉及 research-harness evolution、Propose–Probe–Compose、开发面板、随机合法编辑对照、fixed harness、累计记忆、诊断组件或 mean_abs_ic 时使用；只处理已有派生证据，不执行研究实验。
---

# Auto-Research Paper Writing

将当前 `auto_research` 研究运行转化为来源可核查的英文论文内容。研究事实来自本项目，通用写作、图表和排版工具只提供格式能力。

## 1. 绑定活跃写作运行

用户指定写作运行时，先读其 `README.md`、`all_units.json`、目标 `contexts/*.json`、`evidence/results_packet.json`、`evidence/sources.json` 与 `claim_map.json`；这些文件不存在时，读取运行内实际提供的 `protocol.json`、结果 JSON 和论文 CSV。已有 `all_units.json` 与 `claim_map.json` 时沿用其中的单元标识、主张和来源，不重新设计研究计划，只补齐缺失绑定。尚未建立写作运行时，读取用户指定的 `auto_research` 协议、结果与 `auto_research/docs/DESIGN_CN.md`，再建立新的写作运行。

阅读 `auto_research/docs/WRITING_IMPROVEMENT_PLAN.md` 与 `references/DOMAIN_RULES.md`，使用其中的核心叙事、证据强度契约、项目术语、统计口径、证据分级和结论边界。起草、改写与图注任务同时阅读 `references/STYLE_RULES.md`，删除防御性写作和机械化表达。`llm_driven_paper/writing_skills/current_context.json` 与其他论文的历史上下文不作为本研究的事实来源。

完成条件：研究问题、协议版本、模型身份、实验单位、比较方向、证据角色和当前写作单元均有活跃来源；不同研究运行的结果没有混合。

## 2. 固定证据 seam

只读取结果 JSON、论文 CSV、协议、精简 trace、图表契约和必要的实现文档。写作任务不读取原始市场面板，不调用生产 score/admission，不启动新挖掘，不向模型发送原始行情或封窗内容。数据窗口和独立样本外状态按活跃协议及结果字段表述。

每个定量主张绑定对象、数值、单位、分母、比较方向、实验单位、时间窗、证据角色和来源定位。来源定位使用 JSON pointer、CSV 行选择器或可查找的章节文本；检查定位能够解析到实际字段或记录。

完成条件：目标单元中的每个数字都能回到派生结果；`score_window_read`、`independent_real_data_oos` 与恢复记录按实际值保留。

## 3. 按研究逻辑组织内容

- 方法：解释 Propose–Probe–Compose、原子提案、active branch、frozen champion、probe-only continuation、共同支持终评和信息隔离。
- 对照：区分 scalar、memory、diagnostic、random legal atomic edits 与 fixed harness，说明各臂共享的协议和允许不同的信息。
- 结果：先写固定调用预算下的配对主要分析，再写任务异质性、组件消融、行为代价、执行损失、复现稳定性和真实成本。
- 讨论：将结论限制在活跃 harness、任务集合、开发窗口和预算；空结果、失败和无效提案保持可见。
- 相关工作：原始文献支持具体论断；历史综述只提供检索线索，未核验条目不生成正式引用。

完成条件：每个段落回答一个研究问题；主要分析、辅助分析和诊断没有相互替代；IC 效果与新颖度、重复率等行为变化在相应位置共同呈现。正文采用连贯段落，证据范围只在影响理解的位置准确表达一次，段落结构随证据职责变化。

## 4. 执行当次授权的写作任务

- **准备**：依据活跃运行的实际单元和主张创建或检查上下文、证据包、来源定位、图表契约、布局和审阅计划，不生成论文正文，不另造单元体系。
- **起草或修改**：只处理指定单元及必要相邻上下文，保留已有来源定位和其他单元内容。按照 `references/STYLE_RULES.md` 删除过度限定、重复辩解、预防性澄清、假想质疑、模板化段落、连续编号清单、重复句式、生硬路标和正文中过度使用的 bullets；不为了文风修改证据含义。
- **审阅**：检查实际候选的指标定义、配对方向、统计单位、区间与检验、证据分级、数据边界、引用和跨单元术语；意见单独保存。
- **图表**：复用 `llm_driven_paper/skills/eswa-research-figures` 的导出机制，数据字段、统计单位和图注仍由本研究协议决定。
- **排版**：复用 `llm_driven_paper/skills/eswa-manuscript-layout` 的版式工具，只有内容通过相应审阅后才执行确定性导出。

完成条件：交付物与当次任务类型一致；内容审阅、作者审阅和水印状态分别记录；既有运行和历史稿件未被覆盖。

## 5. 交付前检查

核对实际候选文本和图表，确认：

- `mean_abs_ic`、配对差值方向、任务聚类区间和精确任务检验使用 `references/DOMAIN_RULES.md` 的定义；
- random 臂零模型调用、模型臂真实用量、失败决策和恢复分类与执行记录一致；
- fresh-task development evidence 没有写成独立市场时期 OOS；
- 累计记忆或诊断组件未通过检验时，没有改写成稳定组件优势或已证明零效果；
- 完整执行结果保持辅助分析身份；
- 正文没有叠加弱化、自我强调、重复辩解、预防性澄清或面对假想质疑的解释，证据范围没有逐段重复；
- 正文没有列表化段落、连续编号清单、重复句首、机械路标或每段套用同一结构；
- 文献元数据与引用论断均已核查原始来源。

完成条件：相关检查脚本和实际文本审阅均完成；未检查项目保持明确状态，交付中不写成通过。

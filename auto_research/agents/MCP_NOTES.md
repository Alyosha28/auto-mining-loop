# LM Studio MCP 现状与可选集成

- `~/.lmstudio/mcp.json` 当前只配置了 `web-search-fallback`（本地 zsh bridge，外部搜索；本项目不使用外部搜索取数）。
- 对本系统而言，MCP 的潜在价值不是 web search，而是把**我们的工具**暴露给模型做 tool calling：
  - 例如 factor evaluator（输入 expression → dev IC/ICIR/turnover）、KG retrieval（typed slices）、panel enumerator；
  - 需要：LM Studio 侧 MCP server（stdio）+ 模型 tool-calling 支持 + 在 orchestrator 里走 `/v1/chat/completions` 的 `tools` 参数。
- 当前 orchestrator 已在 Python 侧完成"工具编排"（评估器/KG/表达式解析），所以 MCP 不是必需；若后续要让模型自主选工具（更接近 Co-Scientist 的自适应角色），可加一个本地 MCP server 包装 `expr_search.evaluate_expr` + `knowledge.compose`。
- 注意：任何 MCP 工具都不得接触 score 窗/新时间窗/主库。

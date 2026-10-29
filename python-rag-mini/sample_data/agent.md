# Agent 能力

WeKnora 的 Agent 引擎实现了 ReAct 循环:模型思考(Think)后调用工具(Act),
观察结果后继续思考,直到给出最终答案或达到最大迭代轮数。

Agent 的工具箱包括:知识库检索、网页搜索、待办管理、长期记忆、数据分析等。
技能(Skills)可以从 ClawHub / Git / ZIP 安装,在会话级持久的 Docker / E2B / Cube
沙箱中运行;借助 BrowserSkill 扩展,Agent 还可以操作用户自己的 Chrome / Edge
浏览器;外部 MCP 服务(支持 OAuth)也可以接入,并按工具逐个启用。

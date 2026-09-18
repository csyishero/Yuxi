# 退役内置 Mcp Server Chart

状态：implemented
类型：simplification
Owner：backend/package/yuxi/agents/mcp/service.py

## 问题

`mcp-server-chart` 作为内置 stdio MCP 随服务启动同步到数据库，持续占用系统内置能力与管理界面。当前产品不再提供该内置图表服务。

## 决策

内置 MCP 定义不再包含 `mcp-server-chart`。启动同步把该 slug 视为退役项，仅删除由 `system` 创建的历史记录；用户自行创建的同名远程 MCP 保持不变。

## 替代方案

- 只在界面隐藏：数据库和 Agent 运行时仍能发现并启用该服务，未真正移除能力。
- 只清空默认定义：已有部署中的系统记录会残留，升级后仍会显示。
- 无条件按 slug 删除：会误删管理员自行创建的同名远程服务。

## 后果

升级并重启 API 或 worker 后，系统内置的 Mcp Server Chart 从数据库和管理界面消失。stdio 仍只允许代码维护的内置定义；需要图表能力时可通过 Skill 或管理员配置的远程 MCP 提供。

## 验证

- 启动同步测试证明系统创建的 `mcp-server-chart` 被删除，用户创建的同名远程记录被保留。
- 运行时配置测试证明退役的 stdio 服务不再进入 Agent MCP 配置。
- 旧能力不存在：默认内置定义、数据库同步结果和运行时允许列表均不包含 `mcp-server-chart`。
- 重新引入条件：产品重新确认图表 MCP 是必须随部署内置的受信任能力，并固定、审查其进程包版本与运行边界。

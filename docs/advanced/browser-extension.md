# Browser Extension 与 MCP Gateway

Yuxi 可以通过独立的 Browser MCP Gateway 和 Yuxi Browser Assistant 扩展读取当前用户浏览器中的标签页、语义页面快照、脱敏文本、表格和受控截图。当前版本仅开放读取能力，不包含点击、填写、跳转或提交。

## 组件关系

```text
Yuxi Web ──配对/当前设备断言──> Chrome Extension
   │                                  │
   └──登录态 API──> Yuxi API          └──WSS──> Browser MCP Gateway
                         │                         ▲
                         └──短期 ES256 JWT + MCP──┘
```

网页只负责确认“这个 Run 使用当前浏览器”。用户身份、`run_id`、`thread_id` 和 `request_id` 均由 Yuxi 后端和 Agent 运行上下文提供，模型参数不能覆盖这些字段。

## 1. 准备 ES256 密钥

生成一把 P-256 私钥，并转换成包含 `kid` 的私有 JWK。私有 JWK 只配置给 Yuxi API/Worker；移除 `d` 后的公开 JWK 组成 JWKS，只配置给 Browser Gateway。

示例结构如下，实际值不能提交到 Git：

```json
{
  "kty": "EC",
  "crv": "P-256",
  "kid": "yuxi-browser-2026-09",
  "x": "...",
  "y": "...",
  "d": "..."
}
```

Gateway 使用的公开 JWKS：

```json
{"keys":[{"kty":"EC","crv":"P-256","kid":"yuxi-browser-2026-09","x":"...","y":"..."}]}
```

## 2. 配置 Yuxi

在 `.env` 或 `.env.prod` 中配置：

```dotenv
YUXI_BROWSER_GATEWAY_URL=http://host.docker.internal:8088
YUXI_BROWSER_GATEWAY_PUBLIC_URL=http://localhost:8088
YUXI_BROWSER_ORIGIN=http://localhost:5173
YUXI_BROWSER_EXTENSION_ID=<chrome://extensions 中显示的 ID>
YUXI_BROWSER_GATEWAY_ISSUER=https://yuxi.local
YUXI_BROWSER_GATEWAY_AUDIENCE=browser-gateway
YUXI_BROWSER_GATEWAY_KEY_ID=yuxi-browser-2026-09
YUXI_BROWSER_GATEWAY_PRIVATE_JWK='{"kty":"EC","crv":"P-256","kid":"yuxi-browser-2026-09","x":"...","y":"...","d":"..."}'
YUXI_BROWSER_ASSERTION_TTL_SECONDS=50
```

- `YUXI_BROWSER_GATEWAY_URL` 是 API/Worker 访问 Gateway 的地址。
- `YUXI_BROWSER_GATEWAY_PUBLIC_URL` 是扩展所在浏览器访问 Gateway 的地址。
- `YUXI_BROWSER_ORIGIN` 必须与浏览器地址栏中的 Yuxi origin 完全一致。
- 生产 `YUXI_BROWSER_ORIGIN` 必须使用 HTTPS；HTTP 仅允许 `localhost` 或 `127.0.0.1` 本机开发地址。
- assertion 暂存时间必须为 30–60 秒，且不能超过 Gateway 当前的 60 秒 assertion 有效期。
- Extension ID 不是凭据；unpacked 扩展 ID 变化时，也可在“智能体扩展 > 浏览器”页面本地覆盖。

修改后重新创建 API 和 Worker：

```bash
docker compose up -d --build api worker web
```

## 3. 配置 Gateway 与 Extension

Browser Gateway 的配置必须与 Yuxi 一致：

```dotenv
YUXI_JWT_ISSUER=https://yuxi.local
YUXI_JWT_AUDIENCE=browser-gateway
YUXI_PUBLIC_JWKS='{"keys":[...]}'
YUXI_ALLOWED_ORIGINS=http://localhost:5173
```

具体变量名以 Gateway 的 Spring 配置映射为准。核心要求是 issuer、audience、公开 JWKS 和 allowed origins 四项完全对应。

Extension 必须在加载或打包前生成精确来源白名单，不能使用 `https://*/*`。在扩展目录执行：

```bash
YUXI_BROWSER_ORIGIN=https://yuxi.example.com \
YUXI_BROWSER_GATEWAY_PUBLIC_URL=https://browser-gateway.example.com \
npm run configure:origin
```

该命令会同步更新 `manifest.json`、service worker 使用的 `src/trusted-origins.js` 和 `src/trusted-gateways.js`；生产构建只保留指定 Yuxi 与 Gateway 来源。扩展还会拒绝 Gateway 返回的跨来源 WebSocket 地址。修改后需要在 `chrome://extensions` 重新加载扩展。仓库默认值只覆盖本机开发环境与 `https://yuxi.local`。

## 4. 配对和使用

1. 登录 Yuxi，打开“智能体扩展 > 浏览器”。
2. 填写 Extension ID，点击“检测扩展”。
3. 点击“开始配对”，等待状态变为“已配对 · 在线”。
4. 打开目标 ERP、OA 或 CRM 标签页，点击扩展图标并授权当前网站。
5. 在智能体配置中启用需要的浏览器工具，再发起对话。

每个真实 Agent Run 创建后，Yuxi 页面都会向当前扩展申请一次性 current-device assertion。Yuxi API 校验 Run 归属后会立即用该 Run 身份调用 `browser_current_device`，在 assertion 的 60 秒有效期内固化 Gateway Run 绑定，而不是等模型首次选择浏览器工具。排队请求在收到 `run_created` 事件后才申请，因此不会把 AgentRunRequest 错当成 AgentRun。

同一个 Run 的 assertion 采用首次写入生效：后续浏览器页面不能覆盖已经选定的设备。网页仅在 assertion 有效期内做内存去重；到期后可重新申请，不会把 assertion 或命令结果长期保存到浏览器存储。

## 5. 可用工具

| 工具 | 用途 |
| --- | --- |
| `browser_whoami` | 验证 Gateway 看到的当前 Run 身份 |
| `browser_current_device` | 查看当前 Run 明确绑定的设备 |
| `browser_list_tabs` | 列出标签页并取得 `tab_id` |
| `browser_snapshot` | 获取脱敏语义快照和短期 ref |
| `browser_extract` | 从 ref 提取文本、表格或属性 |
| `browser_screenshot` | 使用同一 Run JWT 下载短期截图，并向模型返回经过类型、大小和 PNG 格式校验的图片内容 |

## 6. 排障

- “未找到扩展”：确认 Extension ID，重新加载扩展，并检查当前 Yuxi origin 是否在 `externally_connectable.matches` 中。
- “当前来源不受信任”：Yuxi、Gateway allowed origins、Extension 受信任来源三处必须完全一致，包括协议与端口。
- “当前浏览器离线”：打开扩展弹窗并点击重新连接，检查 Gateway WSS 地址和证书。
- `DOMAIN_NOT_ALLOWED`：在目标标签页点击扩展并授权当前网站。
- `COMMAND_TIMEOUT`：截图时保持目标标签页活动，避免窗口最小化或页面被后台节流。

日志中不应出现完整 JWT、pairing token、current-device assertion、Cookie、密码或页面敏感内容。

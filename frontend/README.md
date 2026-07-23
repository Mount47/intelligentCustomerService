# SupportFlow Frontend

独立 Vue 3 前端，包含用户聊天端 `/chat` 和管理员端 `/admin`。后端聊天、SSE、指标、工单和会话接口均已接通。开发环境未配置变量时默认使用 mock；设置 `VITE_USE_MOCK=false` 后连接真实后端，接口失败会直接显示错误，不会伪装成 mock 成功。

## 启动

```bash
cd frontend
npm install
npm run dev
```

默认地址：`http://localhost:5173`

## 环境变量

```bash
VITE_API_BASE_URL=/api
VITE_USE_MOCK=false
VITE_ENABLE_MOCK_FALLBACK=false
```

Vite 开发服务器会把 `/api/*` 原样代理到 `http://localhost:8000/api/*`。仅纯 UI 演示需要真实接口失败时回退假数据时，才显式设置 `VITE_ENABLE_MOCK_FALLBACK=true`。

## 已接入后端接口

- `POST /chat/message`
  - 入参：`{ userId, content, clientMessageId }`
  - 出参：`{ sessionId, ticketId?, taskStatus }`
- `GET /chat/session/{id}`
  - 出参字段见 `src/api/types.ts` 的 `ChatSession`
  - 关键：`steps` 用于前端逐步点亮 Agent 时间线；`toolCalls` 用于审计展示
- `GET /chat/session/{id}/stream`（SSE 处理进度）
- `GET /admin/metrics`
- `GET /admin/tickets`
- `GET /admin/tickets/{id}`
- `GET /admin/sessions`
- `GET /admin/sessions/{id}`

字段命名先按前端 camelCase 定义。后端如果保留 Python snake_case，可在接口层统一转换，避免页面组件直接依赖后端内部模型。

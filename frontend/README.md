# SupportFlow Frontend

独立 Vue 3 前端，包含用户聊天端 `/chat` 和管理员端 `/admin`。当前后端 M3-M6 尚未提供完整接口，所以前端默认在开发环境使用 mock 数据；设置 `VITE_USE_MOCK=false` 后会请求真实后端，失败时仍回退 mock，便于渐进联调。

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
VITE_USE_MOCK=true
```

Vite 开发服务器会把 `/api/*` 代理到 `http://localhost:8000`，并去掉 `/api` 前缀。

## 预留后端接口

- `POST /chat/message`
  - 入参：`{ userId, content, clientMessageId }`
  - 出参：`{ sessionId, ticketId?, taskStatus }`
- `GET /chat/session/{id}`
  - 出参字段见 `src/api/types.ts` 的 `ChatSession`
  - 关键：`steps` 用于前端逐步点亮 Agent 时间线；`toolCalls` 用于审计展示
- `GET /admin/metrics`
- `GET /admin/tickets`
- `GET /admin/tickets/{id}`
- `GET /admin/sessions`
- `GET /admin/sessions/{id}`

字段命名先按前端 camelCase 定义。后端如果保留 Python snake_case，可在接口层统一转换，避免页面组件直接依赖后端内部模型。

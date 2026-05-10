# FA Agent API Contract

**Base URL**: `https://agentapi.investarget.com`
**OpenAPI**: `https://agentapi.investarget.com/openapi.json` (live)
**Auth**: JWT Bearer in `Authorization` header for all endpoints except `/api/auth/login` and `/health`.

---

## 1. 通用约定

### 请求头
```
Authorization: Bearer <jwt_token>
Content-Type: application/json
```

### 错误格式
所有非 2xx 响应统一为：
```json
{ "detail": "错误描述（字符串或对象）" }
```

| HTTP | 含义 |
|---|---|
| 400 | 请求参数错误 |
| 401 | 未携带 token / token 无效 |
| 403 | 权限不足 / 账号未开通 |
| 404 | 资源不存在 |
| 422 | 请求体 schema 校验失败（FastAPI 自动） |
| 500 | 服务端异常 |
| 503 | 依赖服务未配置（如 Qiniu、AI） |

---

## 2. 认证

### `POST /api/auth/login`
微信小程序 `wx.login()` 拿到的 `code` 换 token。

**Request**
```json
{ "code": "wx_jscode" }
```

**Response 200**
```json
{
  "token": "eyJhbGciOi...",
  "ir_id": 1,
  "name": "张三",
  "role": "ir"
}
```

**Response 403** — `账号未开通，请联系管理员`（openid 未在 `ir_users` 表中注册）。

> Token 默认 7 天有效期，过期后需重新登录。

---

## 3. 投资人

### `GET /api/investors?stage=&industry=&q=`
分页：当前未分页，全量返回（投资人体量小）。

**Response 200**
```json
{
  "items": [{
    "id": 1,
    "name": "张三",
    "agency": "红杉资本",
    "position": "合伙人",
    "industry_tags": ["AI", "SaaS"],
    "stage_pref": ["A轮", "B轮"],
    "relationship_score": 80,
    "profile_notes": "...",
    "last_interaction_at": "2026-04-15T10:00:00"
  }],
  "total": 1
}
```

### `GET /api/investors/{investor_id}`
返回单个 `InvestorOut`（同上 items 元素结构）。

---

## 4. 日历

### `GET /api/calendar/daily?target_date=2026-05-10`
当日 IR 待办事项（默认今天）。

**Response 200**
```json
{
  "date": "2026-05-10",
  "ir_id": 1,
  "events": [{
    "time": "09:00",
    "type": "followup",          // followup | milestone
    "title": "跟进张三（红杉资本）",
    "description": "上次互动 14 天前",
    "investor_id": 1,
    "investor_name": "张三",
    "action_label": "执行",
    "action_prefill": "帮我跟进张三，生成一条行业推送"
  }]
}
```

> `action_prefill` 直接作为 Agent 输入，前端点"执行"按钮即可发送。

---

## 5. Agent 工作流（核心）

### 5.1 启动工作流

`POST /api/agent/run`

**Request**（不同 task_type 用不同字段，未用到的传 null/省略）
```json
{
  "task_type": "meeting_minutes",   // meeting_minutes | daily_push | smart_list | milestone_outreach
  "audio_url": "https://files.../audio.mp3",   // meeting_minutes 必填
  "transcript": null,                          // 或直接传文字稿替代 audio_url
  "investor_ids": [1, 2],                      // daily_push
  "target_date": "2026-05-10",                 // daily_push
  "criteria": "AI方向A轮投资人",                // smart_list
  "candidate_ids": null,                       // smart_list（可选预筛选）
  "investor_id": 1,                            // milestone_outreach
  "milestone_type": "birthday",                // milestone_outreach: birthday|join_agency|first_meeting
  "ir_name": "张三"                            // milestone_outreach
}
```

**Response 200**
```json
{ "thread_id": "uuid-string" }
```

> 拿到 `thread_id` 后**立刻**开 WebSocket 订阅事件。

### 5.2 订阅事件流

`WS /api/agent/ws/{thread_id}`

**鉴权**（二选一，优先 Header）：
- Header: `Authorization: Bearer <token>`
- Query: `?token=<token>` （小程序 WebSocket 受限场景用）

**鉴权失败行为**：服务端立刻 `close(code=1008, reason=...)`，不接受任何消息。

**事件序列**（每条都是一个 JSON 消息）：

| type | 字段 | 含义 |
|---|---|---|
| `node_done` | `node` | 某个工作流节点完成（用于进度提示） |
| `waiting_review` | `draft`, `task_type` | 等待 IR 审核（暂停） |
| `done` | `final` | 工作流完成 — **WS 主动断开** |
| `error` | `error` | 异常 — **WS 主动断开** |

示例：
```json
{ "type": "node_done", "node": "transcribe" }
{ "type": "node_done", "node": "draft" }
{ "type": "waiting_review", "draft": "纪要草稿……", "task_type": "meeting_minutes" }
{ "type": "done", "final": "最终内容……" }
```

### 5.3 提交 IR 审核结果

`POST /api/agent/{thread_id}/review`

```json
{
  "action": "approve",   // approve | modify | reject
  "final": "（仅 modify 时填）IR修改后的最终文本"
}
```

**Response 200**: `{ "status": "resumed" }`

> 提交后工作流恢复执行，剩余事件会继续从同一个 WS 推送出来（如果还连着）；如果 WS 已断，重连同一 `thread_id` 即可继续接收 `done`/`error`。
>
> **权限校验**：thread 仅其创建者（即 token 对应 ir_id）能 review；他人调用返回 403。

---

## 6. 文件上传（七牛云直传）

### 6.1 申请上传 token

`POST /api/upload/token`

```json
{
  "purpose": "audio",        // audio | image | doc
  "filename": "rec.mp3"      // 可选，用于保留扩展名
}
```

**Response 200**
```json
{
  "token": "qiniu-upload-token...",
  "key": "fa-agent/audio/20260510/ir1/abcdef.mp3",
  "upload_url": "https://upload.qiniup.com",
  "expires_at": 1715342400
}
```

### 6.2 前端直传到七牛

小程序示例：
```js
wx.uploadFile({
  url: upload_url,             // 来自 6.1
  filePath: tempFilePath,
  name: 'file',
  formData: { token, key },    // 必须把 key 也带上
  success(res) { /* 上传成功，使用 6.1 的 key 即可 */ }
})
```

| purpose | MIME 限制 | 大小限制 |
|---|---|---|
| `audio` | `audio/*; video/mp4` | 200 MB |
| `image` | `image/*` | 20 MB |
| `doc` | 不限 | 50 MB |

### 6.3 私有下载签名

`GET /api/upload/sign?key=fa-agent/audio/.../abc.mp3&expires=3600`

**Response 200**
```json
{
  "url": "https://files.your-domain.com/...?e=...&token=...",
  "expires_at": 1715342400
}
```

> Bucket `file` 是私有的，下载必须用签名 URL。`expires` 范围 60–86400 秒。
>
> **配音频/调用 Agent 时**：传 `audio_url` 字段即可（用 `/api/upload/sign` 生成的签名 URL，或直接传 `key` —— 后端在 ASR 时会自动签名，待实现）。

---

## 7. 管理后台（admin role 限定）

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/admin/users` | 创建 IR 用户（无 openid） |
| `POST` | `/api/admin/users/{id}/bind` | 绑定 openid（用户首次登录前管理员调用） |
| `PATCH` | `/api/admin/users/{id}` | 启用/停用 |

需要 token 中 `role=admin`。

---

## 8. 健康检查

`GET /health` → `{"status":"ok"}` —— 不需要 token，不要刷太频。

---

## 附：典型小程序使用流程

**会议纪要场景**：
1. 用户在小程序录音 → 拿到本地 `tempFilePath`
2. `POST /api/upload/token { purpose: "audio", filename: "rec.mp3" }`
3. `wx.uploadFile` 直传七牛
4. `GET /api/upload/sign?key=<上一步的 key>` 拿签名 URL
5. `POST /api/agent/run { task_type: "meeting_minutes", audio_url: <签名URL> }` → 拿 `thread_id`
6. `wx.connectSocket('wss://agentapi.investarget.com/api/agent/ws/<thread_id>?token=<jwt>')`
7. 收到 `waiting_review` 事件 → 弹审核界面 → 用户编辑/批准
8. `POST /api/agent/<thread_id>/review { action, final }`
9. 收到 `done` 事件 → 显示最终结果

**每日推送场景**：
1. 早上 9 点 Celery Beat 已自动跑了 → IR 进小程序看推送 — **不需要前端触发**
2. 看到草稿 → 直接审核 → `POST /api/agent/<thread_id>/review`

> 已知未实现：当前 Beat 任务推送结果落到 `outreach_records` 表，需要新增 `GET /api/outreach/pending` 给前端拉取待审核列表 — 阶段 1 业务需求确认后再补。

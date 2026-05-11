# FA Agent 微信小程序 MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`. 每个 task 用 `- [ ]` 跟踪。
> **Plan mode 产物路径**：`/Users/summer/.claude/plans/polymorphic-soaring-hinton.md`
> **审批通过后**：复制到 `docs/superpowers/plans/2026-05-11-mini-program-mvp.md` 入仓提交

## Context

FA Agent 后端（4 个 LangGraph 工作流 + 认证 + Qiniu）已上线 `https://agentapi.investarget.com`。设计 spec 已 final（`docs/superpowers/specs/2026-05-11-mini-program-design.md`，652 行），需要将其落到执行：

- **后端**：补 18 个接口缺口（约 9 天），核心是手机号绑定登录、投资人 CRUD、腾讯会议 MCP 接入、对话/状态新端点
- **前端**：从零搭微信小程序（约 8.5 天），3 Tab 架构（日程/对话/投资人），含 chat-first 卡片化交互
- **总计**：约 17.5 工作日（单人串行），并行可压到 10-12 天

**目标产出**：可上线的 MVP 小程序 + 后端，5 名 IR 试用，跑通至少 30 条 `status=approved` 草稿。

## Code Reuse Map（已验证存在，必须复用）

| 路径 | 用途 |
|---|---|
| `tests/conftest.py` | 已有 `db_engine` / `db_session` / `override_db` fixture，B3 顺手扩 `authed_client` |
| `backend/models/__init__.py` | 必须 export 任何新 model，否则 alembic autogen + 测试 fixture 看不到 |
| `backend/models/ir_users.py` | `phone` 字段已在，B1 只需加 token 列 + unique 约束 |
| `backend/models/interaction_logs.py` | 表已建，B1 补 3 个字段 |
| `backend/api/agent.py` | WS 鉴权 + Redis owner check 模式，B10 必须复用 |
| `backend/auth/jwt.py` | `decode_token`/`create_token` 已就绪 |
| `backend/redis_client.py` | 异步单例，B3 缓存 session_key 用它 |
| `backend/services/qiniu_service.py` | 已落地的 service 范式参考 |
| `backend/agent/runner.py` | `_checkpointer` + `get_state(config)` 是 B10 的实现关键 |

## File Map

**Create:**
```
backend/services/crypto_service.py         # AES Fernet 加密（B2）
backend/services/tencent_meeting.py        # 腾讯 MCP JSON-RPC 客户端（B11，与旧 skills/ 并存）
backend/api/me.py                          # /api/me + /api/me/tencent/* （B4, B12a, B12b）
backend/api/interactions.py                # 互动记录 CRUD（B7）
backend/api/outreach.py                    # 待审/历史草稿（B8）
backend/agent/nodes/fetch_tencent_minutes.py  # workflow 新节点（B12c）
alembic/versions/{rev}_mvp_schema.py       # 增量迁移（B1）
tests/test_auth_bind.py                    # B3 测试
tests/test_crypto.py                       # B2 测试
tests/test_investor_crud.py                # B6 测试
tests/test_interactions.py                 # B7 测试
tests/test_outreach.py                     # B8 测试
tests/test_calendar_month.py               # B5 测试
tests/test_agent_chat.py                   # B9 测试
tests/test_agent_state.py                  # B10 测试
tests/test_me_endpoints.py                 # B4 + B12 测试
tests/services/test_tencent_meeting.py     # B11 测试
miniprogram/                               # 整个小程序前端（F1-F10）
```

**Modify:**
```
backend/models/ir_users.py             # 加 tencent_meeting_token_encrypted，phone unique（B1）
backend/models/interaction_logs.py     # 加 occurred_at/duration_min/next_followup_at（B1）
backend/config.py                      # 加 SECRET_KEY、TENCENT_MCP_URL（B2, B11）
backend/auth/router.py                 # 改造 login，加 bind_phone（B3）
backend/api/admin.py                   # bind 接口语义改"录手机号"（B3）
backend/api/calendar.py                # 加 /month endpoint（B5）
backend/api/investors.py               # 加 POST/PUT/DELETE（B6）
backend/api/agent.py                   # 加 /chat /state，RunRequest 加 tencent_meeting_id（B9, B10, B12c）
backend/agent/workflows/meeting_minutes.py  # 接入 fetch_tencent_minutes 节点（B12c）
backend/main.py                        # 注册新 router（多次）
tests/conftest.py                      # 加 authed_client fixture（B3）
.env.example                           # 加新变量（B2, B11）
```

---

# Backend Tasks

### Task B1: Alembic 增量迁移 + Model 字段补全

**Files:**
- Modify: `backend/models/ir_users.py`, `backend/models/interaction_logs.py`
- Create: `alembic/versions/{auto}_mvp_schema.py`

- [ ] 在 `IRUser` 加：`tencent_meeting_token_encrypted = Column(LargeBinary(512), nullable=True)`
- [ ] 在 `IRUser` 改：`phone` 字段加 `unique=True`，`wechat_openid` 显式 `nullable=True`
- [ ] 在 `InteractionLog` 加：`occurred_at = Column(DateTime, nullable=False, server_default=func.now())`、`duration_min = Column(SmallInteger, nullable=True)`、`next_followup_at = Column(DateTime, nullable=True)`
- [ ] `cd backend && alembic revision --autogenerate -m "mvp_schema_phone_unique_token_interaction_fields"`
- [ ] 检查生成的迁移文件，确认 op.alter_column / add_column 顺序合理
- [ ] 本地 `alembic upgrade head` 验证可运行
- [ ] 提交：`git commit -m "feat(db): mvp schema — phone unique, encrypted token col, interaction fields"`

**验收**：`SHOW CREATE TABLE ir_users` 显示新列；`SHOW INDEX FROM ir_users` 看到 `phone` UNIQUE。

---

### Task B2: AES 加密服务 + SECRET_KEY 配置

**Files:**
- Create: `backend/services/crypto_service.py`, `tests/test_crypto.py`
- Modify: `backend/config.py`, `.env.example`

- [ ] `config.py` 加：`token_encrypt_key: str = Field(..., min_length=44)` （Fernet key 是 base64 后 44 字节）
- [ ] `.env.example` 加：`TOKEN_ENCRYPT_KEY=` + 注释「用 `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` 生成」
- [ ] `services/crypto_service.py` 实现 `encrypt(plaintext: str) -> bytes` 和 `decrypt(ciphertext: bytes) -> str`，单例 `Fernet(settings.token_encrypt_key.encode())`
- [ ] 异常处理：解密失败抛 `ValueError("token decryption failed — key mismatch?")`
- [ ] 测试：roundtrip + 错误密钥失败 + 空字符串
- [ ] 部署文档加：服务器 `.env` 必须加 `TOKEN_ENCRYPT_KEY`，**密钥丢失则所有用户腾讯 token 失效**
- [ ] 提交：`git commit -m "feat(crypto): AES Fernet service for tencent token encryption"`

**风险**：密钥管理；线上 `.env` 加完后必须备份到 1Password 或类似密钥管理工具。

---

### Task B3: 认证 — 手机号绑定流程

**Files:**
- Modify: `backend/auth/router.py`, `backend/auth/wechat.py`, `backend/api/admin.py`, `backend/redis_client.py` (no change but heavily used), `tests/conftest.py`
- Create: `tests/test_auth_bind.py`

- [ ] `auth/wechat.py` 改 `exchange_code_for_openid` → `exchange_code_for_session(code) -> dict { openid, session_key, unionid }`，调微信 `code2session` 接口（已经在用，只是返回更多）
- [ ] `auth/router.py` 改 `LoginResponse`：联合体（已绑：`{token, ir_id, name, role}` / 未绑：`{need_phone_binding: True, login_session: <uuid>}`）
- [ ] `/api/auth/login` 流程：拿 openid + session_key → 查 ir_users → 已绑返回 token；未绑生成 `login_session = uuid`，把 `{openid, session_key}` 写入 Redis `auth:session:{login_session}` TTL 600s，返回 `need_phone_binding`
- [ ] 新增 `POST /api/auth/bind_phone { login_session, encryptedData, iv }`：从 Redis 拿 session_key → 用 AES-CBC 解密 `encryptedData`（PKCS7 padding，IV 是用户传的）→ 得到 phone → 查 `ir_users.phone` 匹配 → 写 `wechat_openid` → 返回 token / 403
- [ ] `api/admin.py` 改 `POST /api/admin/users` 接收 `phone` 必填（之前是可选），删除 `/users/{id}/bind` 接口（绑定流程已自动化）
- [ ] `conftest.py` 加 fixture：`authed_client(client, db_session)` — 创建测试 IR + 颁发 JWT + 设置 client headers Authorization。下游所有需鉴权 task 复用
- [ ] 测试：login 已绑 / login 未绑 / bind_phone 匹配成功 / bind_phone 不匹配 403 / login_session 过期
- [ ] 提交：`git commit -m "feat(auth): phone-binding login flow + authed_client test fixture"`

**风险**：`session_key` 缓存机制是这个流程的关键。微信小程序的 encryptedData 解密强依赖 session_key（一次性），缓存窗口设短（10 分钟）。

---

### Task B4: GET /api/me

**Files:**
- Create: `backend/api/me.py`, `tests/test_me_endpoints.py`
- Modify: `backend/main.py`

- [ ] `api/me.py` 定义 router，`GET /` 返回当前 IR 信息：`{id, name, phone, role, wechat_openid, tencent_bound: bool}`
- [ ] 注册到 `main.py`：`prefix="/api/me", tags=["me"]`
- [ ] 测试：authed_client 调用返回正确数据 / 无 token 返回 401
- [ ] 提交：`git commit -m "feat(api): GET /api/me endpoint"`

---

### Task B5: GET /api/calendar/month

**Files:**
- Modify: `backend/api/calendar.py`
- Create: `tests/test_calendar_month.py`

- [ ] 加 `GET /month?month=YYYY-MM` 端点，返回 `{ month, days: { "2026-05-11": ["followup", "milestone"], ... } }`
- [ ] 实现复用 `get_daily_calendar` 内部逻辑，按月份遍历每日聚合 type；用单次 SQL 拉所有 `is_active` 投资人，循环匹配每天
- [ ] **性能**：100 投资人 × 31 天 = 3100 次 Python 循环，可接受（毫秒级）。复杂计算才下推到 SQL
- [ ] 测试：当月数据 / 跨年（2025-12）/ 投资人为空
- [ ] 提交：`git commit -m "feat(api): calendar month aggregation endpoint"`

---

### Task B6: 投资人 CRUD

**Files:**
- Modify: `backend/api/investors.py`
- Create: `tests/test_investor_crud.py`

- [ ] 新增 Pydantic schema：`InvestorCreate`, `InvestorUpdate`（部分更新，所有字段可选）
- [ ] `POST /api/investors` 创建（必填 name），返回完整 `InvestorOut`
- [ ] `PUT /api/investors/{id}` 部分更新，自动 `updated_at = now()`
- [ ] `DELETE /api/investors/{id}` 软删（`is_active=false`），返回 `{deleted: true}`
- [ ] 现有 `GET /api/investors` 已过滤 `is_active=true`，无需改
- [ ] 测试：增/改/删 + 改不存在的 id → 404 + 删后列表不再返回
- [ ] 提交：`git commit -m "feat(api): investor CRUD endpoints"`

---

### Task B7: 互动记录端点

**Files:**
- Create: `backend/api/interactions.py`, `tests/test_interactions.py`
- Modify: `backend/main.py`, `backend/models/interaction_logs.py`（如 B1 已加字段则跳过）

- [ ] `POST /api/investors/{investor_id}/interactions { type, occurred_at, duration_min, summary, next_followup_at }`
- [ ] 写入 `interaction_logs`（ir_id 取当前用户）+ 更新对应 investor 的 `last_interaction_at = occurred_at`
- [ ] `GET /api/investors/{investor_id}/interactions?limit=5` 时间倒序返回
- [ ] 注册到 `main.py`：`prefix="/api/investors", tags=["interactions"]` （注意 prefix 与 investors 重叠，FastAPI 会按声明顺序匹配）
- [ ] 测试：建/查 + 投资人不存在 → 404 + last_interaction_at 联动更新
- [ ] 提交：`git commit -m "feat(api): manual interaction logging"`

---

### Task B8: 待审 + 历史草稿端点

**Files:**
- Create: `backend/api/outreach.py`, `tests/test_outreach.py`
- Modify: `backend/main.py`

- [ ] `GET /api/outreach/pending`：当前 IR 名下 `status='draft'` 的 outreach_records，时间倒序，含 `id, type, draft_text, created_at, investor_id`
- [ ] `GET /api/outreach/history?status=&task_type=&limit=20&offset=0`：当前 IR 全部 outreach_records（含 approved/rejected/sent），分页 + 筛选
- [ ] 注册：`prefix="/api/outreach", tags=["outreach"]`
- [ ] 测试：pending 只返 draft / history 全状态 / 不同 IR 隔离 / 分页
- [ ] 提交：`git commit -m "feat(api): outreach pending + history endpoints"`

---

### Task B9: 自由对话端点

**Files:**
- Modify: `backend/api/agent.py`
- Create: `tests/test_agent_chat.py`

- [ ] 加 `POST /api/agent/chat { message, history }`：history 是 `[{role: "user"|"assistant", content: str}]` 最多 10 条
- [ ] 实现：直接调 `skills/claude_skill.py` 的 `claude_generate`，prompt 拼装 `<history> + <current>`
- [ ] 不写 outreach_records、不进 LangGraph、无 review。同步返回 `{reply: str}`
- [ ] 测试：单轮对话 / 带历史多轮（"帮我查张伟" + "他什么职级" 应能识别 "他" = 张伟） / 空 history
- [ ] 提交：`git commit -m "feat(api): free chat endpoint with short context"`

---

### Task B10: GET /api/agent/{thread_id}/state

**Files:**
- Modify: `backend/api/agent.py`
- Create: `tests/test_agent_state.py`

- [ ] 加 `GET /{thread_id}/state` 端点（鉴权 + owner check 复用现有 `/review` 逻辑）
- [ ] 实现：`graph = get_graph(task_type)` → `state = graph.get_state({"configurable": {"thread_id": thread_id}})` → 按 state 推断 status
  - 如果 next 节点是 `review` 且 state.values.draft 存在 → `status="waiting_review", draft=...`
  - 如果 next 为空 → `status="done", final=state.values.final`
  - 如果 state.values.error → `status="error", error=...`
  - 否则 → `status="running", current_node=...`
- [ ] **契约测试**：返回字段必须与 WS `waiting_review` / `done` / `error` 事件 schema 完全一致（前端断线后能直接渲染）
- [ ] 测试：4 种状态 + thread 不存在 → 404 + owner 不匹配 → 403
- [ ] 提交：`git commit -m "feat(api): agent state snapshot for WS reconnect"`

---

### Task B11: 腾讯会议 MCP 客户端

**Files:**
- Create: `backend/services/tencent_meeting.py`, `tests/services/test_tencent_meeting.py`
- Modify: `backend/config.py`

> 已现场验证 MCP endpoint：`https://mcp.meeting.tencent.com/mcp/wemeet-open/v1`，header `X-Tencent-Meeting-Token` + `X-Skill-Version`，body JSON-RPC 2.0。test 用例：`tools/list` 拿到 18 个工具，`get_user_ended_meetings` 返回 8 场会议。

- [ ] `config.py` 加 `tencent_mcp_url: str = "https://mcp.meeting.tencent.com/mcp/wemeet-open/v1"`、`tencent_mcp_skill_version: str = "v1.0.7"`
- [ ] `services/tencent_meeting.py` 类 `TencentMeetingClient(token: str)`：
  - `_call(method, params)` 私有：构造 JSON-RPC body，httpx POST，处理 status/error
  - `verify_token() -> bool`：调 `convert_timestamp` 验证（最轻量）
  - `list_ended_meetings(start_time, end_time, page_size=20) -> list[dict]`
  - `list_upcoming_meetings() -> list[dict]`
  - `get_smart_minutes(record_file_id, lang="zh") -> str`
  - `get_records_list(meeting_id) -> list[dict]`（拿 record_file_id 用）
- [ ] 工具方法 `arguments` 自动加 `_client_info: { os, agent, model }`
- [ ] 错误码处理：`-32603` (tool execution failed) 抛 `TencentToolError(message, raw)`；HTTP 401 抛 `TencentAuthError`
- [ ] 测试：mock httpx，验证 header / body 格式、错误传播。**不调真实 MCP**（避免 token 泄露）
- [ ] 提交：`git commit -m "feat(services): tencent meeting MCP client"`

**重要**：与旧的 `backend/skills/tencent_meeting.py`（server-to-server 版）**并存**，不要替换。旧 skill 用于 schedule_meeting 之类的 server 操作（如有需要），新 service 专门用于 per-IR 数据查询。

---

### Task B12a: PUT/POST /api/me/tencent

**Files:**
- Modify: `backend/api/me.py`
- Modify: `tests/test_me_endpoints.py`

- [ ] `PUT /api/me/tencent { token: str }`：调 `TencentMeetingClient(token).verify_token()` → ok 则 AES 加密存 `ir_users.tencent_meeting_token_encrypted` → 返回 `{ok: true}`；fail 返 400 + 提示
- [ ] `POST /api/me/tencent/test { token: str }`：仅验证不入库（用户保存前预检）
- [ ] 测试：保存成功 / token 无效 / 改错 token 不破坏现有
- [ ] 提交：`git commit -m "feat(api): tencent meeting token configure endpoints"`

---

### Task B12b: GET /api/me/tencent/meetings

**Files:**
- Modify: `backend/api/me.py`
- Modify: `tests/test_me_endpoints.py`

- [ ] `GET /api/me/tencent/meetings?status=ended&days=31`：
  - 从 DB 取当前 IR 的 token → 解密 → 实例化 client
  - status=ended → `list_ended_meetings`；status=upcoming → `list_upcoming_meetings`
  - 返回简化字段：`{ meetings: [{ meeting_id, subject, start_time, end_time, has_recording: bool }] }`
  - `has_recording` 通过 `get_records_list` 检查（缓存到 Redis 5min 减负）
- [ ] 未配置 token → 422 + `{detail: "请先在「我」-「腾讯会议接入」配置 token"}`
- [ ] 测试：成功 / 未配置 / token 失效（mock client 抛错）
- [ ] 提交：`git commit -m "feat(api): list tencent meetings for IR"`

---

### Task B12c: 会议纪要 workflow 接入腾讯纪要

**Files:**
- Create: `backend/agent/nodes/fetch_tencent_minutes.py`
- Modify: `backend/agent/state.py` (加 `tencent_meeting_id` 字段)
- Modify: `backend/agent/workflows/meeting_minutes.py`
- Modify: `backend/api/agent.py` (RunRequest 加字段)
- Create: `tests/agent/test_fetch_tencent_minutes.py`

- [ ] `agent/state.py` 的 `AgentState` 加：`tencent_meeting_id: Optional[str]`
- [ ] `agent/nodes/fetch_tencent_minutes.py`：
  - 输入 state，无 `tencent_meeting_id` 直接 return `{}`（不影响后续 transcribe 节点）
  - 有则取 IR token → client.get_records_list → 拿到 record_file_id → client.get_smart_minutes → 写入 `state.transcript`
  - 失败（无录制）→ 抛 `RuntimeError("会议未开云录制")`，由 workflow 捕获
- [ ] 修改 `meeting_minutes.py`：在 `transcribe` 节点前加 `fetch_tencent_minutes`，边为 `START → fetch_tencent_minutes → transcribe → ...`
- [ ] `api/agent.py` 的 `RunRequest` 加 `tencent_meeting_id: Optional[str] = None`，`/run` 端点构造 state 时透传
- [ ] 测试：有 tencent_meeting_id 且有纪要 / 有 id 但无录制（应抛错走错误事件） / 无 id 走原路径不影响
- [ ] 提交：`git commit -m "feat(workflow): meeting_minutes prefer tencent smart minutes"`

---

# Frontend Tasks

> 整个 `miniprogram/` 目录新建。微信开发者工具创建项目时选「不使用云开发」「使用 TypeScript」（强烈建议，spec 里所有 schema 用 TS interface 描述能省 30% 联调时间）。

### Task F1a: 项目脚手架 + tabBar + utils

**Files:**
```
miniprogram/
├── app.ts / app.json / app.wxss
├── pages/index/  (placeholder, splash 之前)
├── utils/storage.ts / time.ts
└── project.config.json
```

- [ ] 微信开发者工具创建 TypeScript 项目，AppID 用测试号或公司测试版 AppID
- [ ] `app.json` 配置 tabBar：
  - 三个 tab：`pages/calendar/index`（默认）、`pages/chat/index`、`pages/investors/index`
  - 颜色 + iconfont（先用 emoji 占位，后期换 SVG）
- [ ] `app.wxss` 全局样式 + Agent 颜色 CSS 变量（`--agent-orchestrator: #6B7AFF; --agent-list: #8B5CF6; --agent-content: #374151; --agent-outreach: #F59E0B`）
- [ ] `utils/storage.ts`：`get/set/del` 包装 wx storage，自动 JSON
- [ ] `utils/time.ts`：`formatRelative(date)` 返回 "刚刚 / N 分钟前 / N 天前"
- [ ] 提交：`git commit -m "feat(fe): mini-program scaffold + tabBar + utils"`

---

### Task F1b: API 服务（JWT + 401 + 错误处理）

**Files:**
- Create: `miniprogram/services/api.ts`, `miniprogram/services/auth.ts`

- [ ] `api.ts` 暴露 `request<T>(path, opts) -> Promise<T>`：
  - 自动加 `Authorization: Bearer <jwt>` from storage
  - 401 → 清 jwt → 跳 splash 页（global navigator）
  - 5xx → wx.showToast "服务器繁忙"
  - 网络错误 → wx.showToast "网络异常，请重试"
- [ ] `auth.ts` 暴露 `login(code)`、`bindPhone(login_session, encryptedData, iv)`、`logout()`、`getCurrentUser()` 缓存版
- [ ] 提交：`git commit -m "feat(fe): api service with JWT + 401 handling"`

---

### Task F1c: WS 服务（重连 + state 协议）

**Files:**
- Create: `miniprogram/services/ws.ts`

- [ ] `WSManager` 类（单例）：`subscribe(thread_id, onEvent)` / `unsubscribe(thread_id)`
- [ ] 内部用 `wx.connectSocket` 连 `wss://agentapi.investarget.com/api/agent/ws/{thread_id}?token=jwt`
- [ ] 重连：`onSocketClose` → 1s/3s/8s 退避；3 次失败后调 `GET /api/agent/{thread_id}/state` 拉快照，触发 `onEvent({ type: "snapshot", ...state })`
- [ ] 收到 `done` 或 `error` 事件 → 主动 close，不重连
- [ ] **契约测试**：写一个小的 demo 页验证 WS 断开 → 重连 → 拉快照 → 完整事件流
- [ ] 提交：`git commit -m "feat(fe): WebSocket manager with reconnect + state fallback"`

---

### Task F2: 登录 + 手机号绑定

**Files:**
```
miniprogram/pages/splash/  (启动页)
miniprogram/pages/bind-phone/  (绑定页)
```

- [ ] `splash`：紫渐变 + "FA" logo + "进入工作台" 按钮 → `wx.login()` → POST `/api/auth/login`
  - `{token}` → 存 jwt → `wx.switchTab` 到 calendar
  - `{need_phone_binding, login_session}` → 跳 bind-phone（带参数）
- [ ] `bind-phone`：`<button open-type="getPhoneNumber" bindgetphonenumber="onPhone">` 拿 `e.detail.encryptedData/iv` → POST `/api/auth/bind_phone`
  - 200 → 存 jwt → 跳 calendar
  - 403 → 显示"账号未开通，请联系管理员"
- [ ] 首次进入设置 storage `mro:onboarded=true`，下次跳过 splash 直接 `wx.switchTab`
- [ ] 提交：`git commit -m "feat(fe): splash + phone binding pages"`

---

### Task F3: 日程 Tab（月 + 日）

**Files:**
```
miniprogram/pages/calendar/      (月视图)
miniprogram/pages/calendar-day/  (日视图)
```

- [ ] `calendar/index` 月视图：自绘日历（不用 picker），每个日期格子下方根据 `GET /api/calendar/month` 显示彩色点
- [ ] 顶部右上角头像 → 点击跳 `/pages/me/`
- [ ] 日期切换不重新拉数据（缓存到 page data），切月才拉
- [ ] 当月预览列表（最多 3 条今日事件，调 `GET /api/calendar/daily`）
- [ ] 点某日 → `wx.navigateTo` 到 `/pages/calendar-day/?date=YYYY-MM-DD`
- [ ] `calendar-day` 日视图：完整事件卡片列表，按 type 渲染按钮
  - `followup` → `[执行 →]` → POST `/api/agent/run` + `wx.switchTab` 到 chat
  - `meeting` → `[纪要准备]` → 跳 `/pages/meeting-prepare/?meeting_id=xxx`
  - `milestone` → `[审核]` → 找 outreach_records 草稿 → `wx.switchTab` 到 chat 显示审核卡
- [ ] 提交：`git commit -m "feat(fe): calendar tab — month + day views"`

---

### Task F4a: 对话 Tab 骨架 + agent-card 组件（静态）

**Files:**
```
miniprogram/pages/chat/
miniprogram/components/agent-card/
miniprogram/components/thinking/
```

- [ ] `chat/index` 布局：顶部 FA Agent + 头像、中部消息流（用户气泡右侧 / Agent 卡片左侧）、底部输入框
- [ ] `agent-card` 组件：props `{ agent: 'orchestrator'|'list'|'content'|'outreach', title, body (slot), actions: [{label, type}] }`
- [ ] 颜色根据 agent 选 CSS 变量
- [ ] 静态展示 mock 数据（先不接 API）
- [ ] `thinking` 组件：`[X] X Agent · 正在思考` + 三点 loading
- [ ] 提交：`git commit -m "feat(fe): chat skeleton + agent-card component"`

---

### Task F4b: 对话 Tab WS 集成 + Orchestrator 早安卡

**Files:**
- Modify: `miniprogram/pages/chat/index.ts`

- [ ] 进入页面：调 `GET /api/calendar/daily` 拉今日数据 → 注入 Orchestrator 早安卡（首条静态消息）
- [ ] 用户输入 → POST `/api/agent/chat`（自由对话，前端维护 10 条 history） → 显示回复气泡
- [ ] 工作流触发场景：从外部跳进来时（路由参数 `from_thread=xxx`）→ 启动 WS 订阅，根据事件类型动态插入卡片：
  - `node_done` → 更新 thinking 文本
  - `waiting_review` → thinking 卡变成 agent-card（短内联编辑）
  - `done` → 卡片加"已通过"标记，按钮消失
  - `error` → 红色错误卡 + 重试按钮
  - `snapshot`（来自 state fallback）→ 按 status 直接渲染最终态
- [ ] 提交：`git commit -m "feat(fe): chat WS integration + Orchestrator greeting"`

---

### Task F4c: 短内联审核交互

**Files:**
- Modify: `miniprogram/components/agent-card/`
- Create: `miniprogram/components/review-modal/` (短内联，不全屏)

- [ ] agent-card 检测 body 长度 < 200 字 → 内联展开模式（点"调整"展开 textarea）
- [ ] 三按钮：`通过` POST `/review { action: "approve" }`、`调整` 展开编辑后 POST `{ action: "modify", final }`、`拒绝` POST `{ action: "reject" }`
- [ ] 点击后按钮置 disabled + loading
- [ ] 收到 WS `done` 事件 → 卡片折叠 + "✓ 已通过" 标记
- [ ] 提交：`git commit -m "feat(fe): inline short-content review interaction"`

---

### Task F5: 投资人 Tab 列表 + 详情

**Files:**
```
miniprogram/pages/investors/
miniprogram/pages/investor-detail/
miniprogram/components/investor-tile/
```

- [ ] `investors/index`：搜索框 + 横滚标签筛选 + 列表
- [ ] 标签状态本地保存（storage `investors:filter`）
- [ ] 拉 `GET /api/investors?stage=&industry=&q=`
- [ ] 点卡片 → `wx.navigateTo` 详情
- [ ] `investor-detail`：紫渐变头部 + 关系值（点点修改本地，待编辑保存才提交）+ 标签 chips + AI 偏好画像 + 近期互动（拉 `GET /api/investors/{id}/interactions?limit=5`）
- [ ] 底部按钮：`✦ 问 Agent 关于 X` → switchTab chat + 自动发问句 + 触发 `daily_push` workflow
- [ ] 右上角"编辑"→ 跳 investor-edit 页
- [ ] 提交：`git commit -m "feat(fe): investor list + detail pages"`

---

### Task F6: 投资人新增/编辑

**Files:**
```
miniprogram/pages/investor-edit/
```

- [ ] 路由 `/pages/investor-edit/?id=X`（id 为空 = 新增）
- [ ] 表单字段：姓名*、机构、职务、关系值、手机/微信/邮箱、行业标签、阶段标签、单笔范围、生日、入职机构日期、AI 偏好画像
- [ ] 保存：POST `/api/investors`（新增）或 PUT `/api/investors/{id}`（编辑）
- [ ] 编辑模式底部加 `[删除]`，二次确认 → DELETE
- [ ] 提交：`git commit -m "feat(fe): investor edit + create page"`

---

### Task F7: 手动记互动

**Files:**
```
miniprogram/pages/interaction-new/
```

- [ ] 路由 `/pages/interaction-new/?investor_id=X`
- [ ] 表单：投资人（已选 + 「更换」）、type radio、occurred_at 时间选择、duration_min（type 为 phone/meeting 才显示）、summary、next_followup_at（含 `+ 加入日历` checkbox）
- [ ] 保存 POST `/api/investors/{id}/interactions`
- [ ] 成功 → wx.navigateBack 或跳详情页
- [ ] 提交：`git commit -m "feat(fe): manual interaction logging"`

---

### Task F8: 会议纪要准备页（3 模式）

**Files:**
```
miniprogram/pages/meeting-prepare/
```

- [ ] 三个卡片选择源：
  1. 从腾讯会议拉取 → 拉 `GET /api/me/tencent/meetings` → 弹 actionsheet 选 → POST `/api/agent/run { task_type: "meeting_minutes", tencent_meeting_id }`
  2. 上传音频文件 → `wx.chooseMessageFile` → 走 Qiniu 上传链路（`POST /upload/token` → `wx.uploadFile` → `GET /upload/sign`）→ POST `/api/agent/run { audio_url }`
  3. 粘贴文字稿 → textarea（含警示「>5000 字建议改用音频」）→ POST `/api/agent/run { transcript }`
- [ ] 提交后：拿 thread_id → switchTab chat → 带 `from_thread=xxx` 参数让 chat 自动订阅 WS
- [ ] 「未配置腾讯接入」时模式 1 灰禁 + 引导跳腾讯设置页
- [ ] 提交：`git commit -m "feat(fe): meeting prepare with 3-tier source selection"`

---

### Task F9: 我 + 腾讯会议接入

**Files:**
```
miniprogram/pages/me/
miniprogram/pages/tencent-setup/
```

- [ ] `me`：个人信息卡 + 列表项（腾讯会议接入 / 草稿历史 / 退出登录）
- [ ] `tencent-setup`：教程引导（点击打开 `https://meeting.tencent.com/ai-skill`）+ Token 输入框 + `[测试连接]` `[保存]`
- [ ] `[测试]` POST `/api/me/tencent/test`，`[保存]` PUT `/api/me/tencent`
- [ ] 底部明显提示「**拉取智能纪要需要会议开启云录制**」+ 「[如何开启云录制 →](https://meeting.tencent.com/support/topic/1853/index.html)」
- [ ] 提交：`git commit -m "feat(fe): me + tencent setup pages"`

---

### Task F10: 长内容审核 Modal + 草稿历史

**Files:**
```
miniprogram/components/review-full-modal/
miniprogram/pages/drafts/
```

- [ ] `review-full-modal` 全屏组件：顶部 Agent 信息 + 中部可编辑富文本（用 `<textarea>` 即可，富文本 MVP 不做） + 底部 `[保存草稿]` `[通过并发送]` + 顶部右上 `通过/拒绝`
- [ ] agent-card 检测 body 长度 ≥ 200 字时点"调整"→ 弹此 Modal
- [ ] `drafts` 页：拉 `GET /api/outreach/history`，时间倒序 + 顶部 chip 筛选 task_type
- [ ] 点条目 → 进入对应审核界面（pending → 弹 Modal；其他状态 → 只读详情）
- [ ] 提交：`git commit -m "feat(fe): full review modal + drafts history page"`

---

# Integration Tasks

### Task I0: 联调种子数据

**Files:**
- Create: `scripts/seed_mvp.sql` 或 `scripts/seed_mvp.py`

- [ ] 5 个测试 IR 用户（含 phone）
- [ ] 30 个 outreach_records，覆盖 4 种 task_type、3 种 status (draft/approved/rejected)
- [ ] 20 个 investor，含 birthday 在测试期内的 5 个（触发 milestone）
- [ ] 提交：`git commit -m "chore: MVP seed data script"`

---

### Task I1: 端到端联调 + 弱网测试

- [ ] 部署后端到 staging（如有，否则直接 prod）
- [ ] 跑通 4 个工作流（每个工作流用 1 名 IR + 1 个 investor）
- [ ] 跑通投资人全 CRUD + 互动记录
- [ ] **弱网测试**：
  - WS 断线后审核卡是否能从 state snapshot 正确渲染
  - 切前后台 30s 后重连
  - 4G/3G 模拟
- [ ] 修复发现的 bug
- [ ] 提交 release notes 到 GitHub release

---

# Critical Path & Parallelization

```
强串行：B1 → B2 → B3 → 所有需鉴权后端 task
       F1a → F1b → F1c → F2 → 所有 F 页面
       B11 → B12a/b/c → F8

可并行：
  Wave 1 (after B3):  B4, B5, B6, B7, B8, B9, B10  ← 全部彼此独立
  Wave 2 (after F2):  F3, F5, F6, F7, F9, F10        ← 全部彼此独立
  Wave 3:             F4a → F4b → F4c, F8           ← chat 是关键路径
```

**关键路径（决定总工期）**：B1 → B3 → F2 → F4a → F4b → F4c → I1 ≈ **11 天**
**单线程总工期**：~17.5 天
**双人并行（前后端各 1）**：~10 天
**三人并行（前后端 + 测试）**：~8 天

---

# Verification

每个 task 提交前必须满足：

1. **后端 task**：
   - 新接口至少 3 个测试用例（happy path / 边界 / 错误）
   - `cd backend && pytest tests/test_<module>.py -v` 全绿
   - alembic 迁移可双向（`upgrade head` + `downgrade -1`）
   - `git diff` 检查无遗漏 print/debug 代码

2. **前端 task**：
   - 微信开发者工具运行无 console error/warning
   - 至少在真机预览过一次
   - 关键交互 wechat dev tool 录屏存档

3. **集成验收**（I1 完成后）：
   - 5 名内测 IR 各完成至少 1 个完整工作流
   - 后端 `outreach_records` 表 ≥ 30 条 `status=approved`
   - 0 个 P0 bug（卡死/数据丢失/鉴权绕过）
   - 弱网场景（4G + 主动断线）验证通过

---

# Risk Register

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 企业认证小程序未及时下来 | 中 | 阻塞 wx.getPhoneNumber | 用测试版 AppID 联调，预留 1 周缓冲 |
| `session_key` 缓存策略错 | 中 | bind_phone 失败 | B3 必须有 e2e 测试，模拟真实小程序加密 payload |
| Tencent MCP token 过期机制不明 | 中 | 用户隔几天就要重配 | 前端检测 401 → 引导跳腾讯设置页，token 永久有效则忽略 |
| LangGraph state schema 变化 | 低 | B10 snapshot 接口失效 | 契约测试 + 前端 graceful fallback |
| Fernet 密钥丢失 | 低 | 全用户腾讯 token 失效 | 部署前生成 + 立刻备份到 1Password |
| 月历聚合性能退化 | 低 | 投资人多了之后慢 | 监控接口 latency，>500ms 改 SQL 实现 |
| 微信认证企业主体审核失败 | 低 | MVP 上线推迟 | 提前确认营业执照 + 对公账户 |

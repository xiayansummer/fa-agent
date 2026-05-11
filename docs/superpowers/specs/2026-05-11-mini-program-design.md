# FA Agent 微信小程序设计 Spec

**日期**：2026-05-11
**状态**：待评审
**前置**：
- 后端 API 契约：`docs/api-contract.md`
- 原型参考：`/Users/summer/Downloads/FA Agent/FA Agent.pdf`（6 页）
- 后端实现：`docs/superpowers/plans/2026-04-22-langgraph-agent-system.md`

## 1. 目标与非目标

### 目标
让 IR（投资人关系）人员通过微信小程序完成日常工作的全部 Agent 协作：会议纪要、每日推送、智能名单、节点关怀。MVP 上线交付一个**信息沉淀 + 录入提效**的可用形态。

### 非目标（MVP 不做）
- 推送通知（订阅消息/企业微信）—— 阶段 2 再加
- WebSocket 重连后的事件回放 —— 完成后只能从 outreach_records 历史看
- 多公司/多团队隔离 —— 单租户假设
- IR 自助导入投资人 —— 数据由后台导入或同步
- 投资人匹配/反馈归因/交易推进 Agent —— 阶段 2-3
- 现场录音 —— MVP 只支持文件上传

## 2. 用户角色

| 角色 | 权限 | 来源 |
|---|---|---|
| **IR**（投资人关系经理） | 使用所有 Agent 工作流、查看自己的草稿和投资人 | 管理员后台预先创建并绑 openid |
| **Admin** | IR 全部权限 + 管理 IR 用户、绑 openid | 数据库 `role='admin'` |

> 首次登录用 `wx.login()`，未在 `ir_users` 表注册的 openid 无法登录。

## 3. 整体架构

### 3.1 导航结构（3 Tab）

```
┌────────────────────────────────────────┐
│       (Active Page)                    │
│                                        │
├────────────────────────────────────────┤
│  📅 日程 (默认)   💬 对话   👥 投资人    │
└────────────────────────────────────────┘
```

**默认 Tab = 日程**（IR 习惯先看日历）。

「我」入口：每个 Tab 右上角显示 IR 头像（首字母圆角），点击进入「我」页（侧滑或跳转）。

### 3.2 页面树

```
启动页（仅首次） ─→ 日程 Tab
                     │
                     ├─ 日程详情（某天）
                     │   └─ [执行/纪要准备/审核] ─→ 跳到对话 Tab
                     │
对话 Tab ─────────── (Agent 卡片消息流)
                     │
                     ├─ 长内容审核 Modal（全屏）
                     ├─ 会议纪要准备页 ─→ 上传/拉取/粘贴 ─→ 回对话
                     │
投资人 Tab ────────── (列表+筛选)
                     │
                     └─ 投资人详情
                         └─ [问 Agent] ─→ 跳对话 Tab

我（右上角入口）─────
                     ├─ 腾讯会议接入设置
                     ├─ 我的草稿历史
                     └─ 退出登录
```

## 4. 核心交互模式

### 4.1 Chat-first + 4 Agent 身份

对话 Tab 是核心。所有 Agent 工作的展现都在这里发生（包括从其他 Tab 触发的）。

| Agent | 颜色 | 头像字符 | 职责 |
|---|---|---|---|
| Orchestrator · 统筹 | 蓝紫 `#6B7AFF` | O | 早安、任务汇总、引导跳转 |
| 名单 Agent | 紫 `#8B5CF6` | 名 | 智能筛选投资人 |
| 内容 Agent | 灰 `#374151` | 内 | 会议纪要、行业推送、关怀文案 |
| 触达 Agent | 橙 `#F59E0B` | 触 | 发送/记录触达动作（阶段 2 真实发送） |

### 4.2 消息形态

```
┌─ 文本气泡 ────────────────────┐
│ 用户：帮我跟进张伟              │
└────────────────────────────────┘
┌─ Agent 卡片 ──────────────────┐
│ [O] Orchestrator              │
│ ┌────────────────────────────┐│
│ │ 早上好，今日有 3 个任务     ││
│ │ ┌──┐ ┌──┐ ┌──┐            ││
│ │ │3 │ │5 │ │2 │            ││
│ │ │待│ │跟│ │会│            ││
│ │ │审│ │进│ │议│            ││
│ │ └──┘ └──┘ └──┘            ││
│ └────────────────────────────┘│
└────────────────────────────────┘
┌─ 思考态 ──────────────────────┐
│ [内] 内容 Agent · 正在思考     │
│ ●●●                           │
└────────────────────────────────┘
```

每张 Agent 业务卡片底部带 3 个动作按钮（对应后端 `IrAction`）：
`通过` / `调整` / `拒绝`

## 5. 页面详细设计

### 5.1 启动页（仅首次）

- 全屏紫色渐变 + "FA" logo
- 标题：「你的 IR 工作台 / 由 4 个 Agent 协同完成」
- 副标题：「Orchestrator 每日为你规划日程，名单/内容/触达 Agent 主动推送草稿，你只需在对话中一键审核」
- 按钮：`进入工作台 →`
- 行为：点击 → 调用 `wx.login()` → POST `/api/auth/login` → 存 JWT 到 storage（key: `mro:jwt`）→ 设置 `mro:onboarded=true` → 跳到日程 Tab

### 5.2 日程 Tab

#### 月视图（默认进入）
- 顶部：标题「我的日程 ✦ AI 生成」+ 右上角头像
- 中部：月历（小程序原生 `<picker>` 或自绘）
  - 每个日期格子下方显示彩色圆点：`● 跟进 ● 会议 ● 里程碑 ● 推送`（4 色对应 `event.type`）
- 下方：当日事件预览列表（最多 3 条，更多展开）
- 数据：`GET /api/calendar/daily?target_date=YYYY-MM-DD`（首屏拉今天 + 当月所有日的事件聚合）

#### 日视图（点某天进入）
- 顶部：「2026-04-09 · 周四」+ 「问 Agent」 按钮
- "✦ Agent 已为你规划 N 项任务"
- 事件卡片列表，每张：
  - 时间徽标 + 类型标签
  - 标题（投资人/会议名）+ 副标题
  - **动作按钮**（按 `event.type` 决定）：
    - `followup` → `[执行 →]`
    - `meeting` → `[纪要准备]`
    - `milestone` → `[审核]`（草稿已由 Beat 自动生成）

#### 数据流（日程 Tab → 对话 Tab）
点任一动作按钮：
1. 切到对话 Tab
2. 自动发一条用户消息（系统态，灰底）："来自日程：执行跟进张伟（高榕资本）"
3. 触发对应工作流：
   - `followup` → POST `/api/agent/run { task_type: "daily_push", investor_ids: [X], target_date }`
   - `meeting` → 跳到「会议纪要准备」页（5.5）
   - `milestone` → 找最近的 outreach_records 草稿展示审核卡（不重新触发）

### 5.3 对话 Tab（核心）

#### 布局
```
┌────────────────────────────────────────┐
│ FA Agent                       [头像]   │
├────────────────────────────────────────┤
│                                        │
│  [O] Orchestrator                      │
│  早上好，今日有 3 任务...               │
│                                        │
│  [名] 名单 Agent                        │
│  今日跟进名单 5 人...                   │
│  [✓ 确认] [调整] [拒绝]                 │
│                                        │
│                帮我写张伟的会议纪要 ◯  │
│                                        │
│  [内] 内容 Agent · 正在思考             │
│  ●●●                                   │
│                                        │
├────────────────────────────────────────┤
│ [+] 输入文字...               [发送]   │
└────────────────────────────────────────┘
```

#### 进入时
- 拉对话历史：本地 storage 存 `chat:history:{ir_id}`（有限：最多 50 条，超出滚动加载）
- **首次/无历史**：注入一条 Orchestrator 早安卡（数据来自 `GET /api/calendar/daily` 的统计）
- WebSocket：如果 storage 里有 `current_thread_id`，尝试连 `wss://.../ws/{thread_id}?token=jwt`，连不上则放弃（MVP 不重连）

#### 发送消息

自由对话和工作流触发走两条不同路径：

- **自由对话**（用户在底部输入框打字/发问）：POST `/api/agent/chat { message }`（**新增接口**，见接口缺口 #7）
  - 后端单独的轻量 LLM 处理，不进入 4 个工作流，无 review
  - 用于：解释概念、查信息、闲聊、引导
- **工作流触发**（从卡片按钮、日程页跳转）：POST `/api/agent/run { task_type, ... }`
  - 显式 task_type，进入对应 LangGraph 工作流
  - 有 review 节点，会推送审核卡

> 这种分离避免了"前端猜 task_type"的复杂性，也让自由对话可以做得更轻量（不写 outreach_records）。

#### 卡片按钮交互
| 按钮 | 行为 |
|---|---|
| `通过` | POST `/api/agent/{thread_id}/review { action: "approve" }` |
| `调整`（短内容 < 200 字） | 卡片内联展开编辑框 → 编辑后 POST `{ action: "modify", final: <文本> }` |
| `调整`（长内容 ≥ 200 字） | 弹全屏 Modal（5.4）|
| `拒绝` | POST `{ action: "reject" }` |

#### Agent 思考态
- WS 收到 `node_done` 事件 → 三点 loading 文字变成 `"内容 Agent · 正在思考（已完成 transcribe）"`
- WS 收到 `waiting_review` → 思考卡变成正式卡片 + 3 按钮
- WS 收到 `done` → 草稿卡变成最终卡片（去掉按钮，加"已通过"标记）
- WS 收到 `error` → 红色提示卡，附"重试"按钮

### 5.4 长内容审核 Modal（全屏）

```
┌────────────────────────────────────────┐
│ ◯ 关闭                  ✓ 通过 / 拒绝  │
├────────────────────────────────────────┤
│ [内] 内容 Agent  ·  会议纪要草稿        │
│ 来自：腾讯会议 · 李明 · 45min           │
├────────────────────────────────────────┤
│                                        │
│ # 会议要点                              │
│ 1. ...                                  │
│ 2. ...                                  │
│                                        │
│ # 行动项                                │
│ - ...                                   │
│                                        │
│ (可编辑富文本区)                        │
│                                        │
├────────────────────────────────────────┤
│            [保存草稿] [通过并发送]       │
└────────────────────────────────────────┘
```

- 顶部右侧 `通过` / `拒绝` 给"我看完了，不改"的快速通道
- 底部 `保存草稿`（提交 `modify`，状态保持待审核）/ `通过并发送`（提交 `modify` + `approve`，前端串两次 API）

### 5.5 会议纪要准备页（独立路由 `/pages/meeting/prepare`）

```
┌────────────────────────────────────────┐
│ < 准备会议纪要                           │
├────────────────────────────────────────┤
│ 选择音频/文字来源：                      │
│                                        │
│ ┌──────────────────────────────────┐  │
│ │ 🎯 从腾讯会议拉取                │  │
│ │   (需先配置接入)                  │  │
│ │                       [选择会议→] │  │
│ └──────────────────────────────────┘  │
│                                        │
│ ┌──────────────────────────────────┐  │
│ │ 📎 上传音频文件                  │  │
│ │   支持 mp3/m4a/wav，最大 200MB    │  │
│ │                       [选择文件→] │  │
│ └──────────────────────────────────┘  │
│                                        │
│ ┌──────────────────────────────────┐  │
│ │ ✍️ 粘贴文字稿                    │  │
│ │   (展开输入框)                    │  │
│ └──────────────────────────────────┘  │
│                                        │
└────────────────────────────────────────┘
```

#### 模式 1：从腾讯会议拉取
1. 调用 `GET /api/me/tencent/meetings?status=ended&days=31` 拉历史会议列表
2. 弹底部 actionsheet 让 IR 选某场
3. 选中后 → POST `/api/agent/run { task_type: "meeting_minutes", tencent_meeting_id: "xxx" }`（**新增字段**）
4. 后端：通过 IR 的 token 调腾讯 MCP `get_smart_minutes` → 拿到智能纪要 → 走 meeting_minutes workflow
5. **失败处理**：腾讯返回"无录制" → 后端返回 422，前端弹 toast「该会议未开云录制，请改用上传文件或粘贴文字」

#### 模式 2：上传音频文件
1. `wx.chooseMessageFile({ type: 'file', count: 1, extension: ['mp3','m4a','wav'] })` 拿到 tempFilePath
2. POST `/api/upload/token { purpose: "audio", filename }` → 拿 token
3. `wx.uploadFile({ url: upload_url, filePath, name: 'file', formData: { token, key } })`
4. 拿到 key → GET `/api/upload/sign?key=` → 拿签名 URL
5. POST `/api/agent/run { task_type: "meeting_minutes", audio_url: 签名URL }`

#### 模式 3：粘贴文字稿
1. 展开 textarea → IR 粘贴
2. POST `/api/agent/run { task_type: "meeting_minutes", transcript: <文本> }`

#### 提交后
所有 3 种模式提交后 → 跳到对话 Tab，订阅 WS 看 Agent 处理 → 弹审核卡。

### 5.6 投资人 Tab

#### 列表页
- 顶部：标题「投资人库」+ 右上角头像 + `+ 新增`（admin 才显示，MVP 暂不实现新增功能）
- 搜索框（query: `q`）
- 横向滚动标签条：A轮/B轮/C轮/医疗/AI/SaaS/...（多选，标签状态本地保存）
- 投资人卡片列表（按关系热度排序，由后端返回顺序决定）
- 数据：`GET /api/investors?stage=&industry=&q=`
- 底部：`共 N 位投资人 · ✦ AI 已按关系热度排序`

#### 详情页（`/pages/investor/detail?id=X`）
- 头部：紫渐变 + 头像（首字符）+ 名字 + 机构·职务
- 关系值：5 点
- 标签 chips
- "AI 偏好画像"区：`profile_notes` 解析为多条 `[MM-DD] 文字`
- "近期互动"区：从 `interaction_logs` 表拉，`GET /api/investors/{id}/interactions?limit=5`（**新接口**）
- 底部 sticky 大按钮：`✦ 问 Agent 关于 张伟`
- 点按钮 → 跳对话 Tab，自动发：`"展开张伟（高榕资本）的近况和建议跟进方式"`，触发 `task_type=daily_push, investor_ids=[X]` 工作流

### 5.7「我」页面

```
┌────────────────────────────────────────┐
│ < 我                                    │
├────────────────────────────────────────┤
│  ┌──┐                                  │
│  │张│  张三  · 高级投资经理            │
│  └──┘  openid: ox..ABCD                │
├────────────────────────────────────────┤
│ 🎯 腾讯会议接入            [未配置 >]  │
│ 📋 我的草稿历史                     >  │
│ ⚙️ 退出登录                            │
└────────────────────────────────────────┘
```

- 数据：`GET /api/me`（**新接口**）
- "腾讯会议接入" → 跳 5.8
- "我的草稿历史" → 跳「草稿历史」页（列出所有 outreach_records，按时间倒序）
- "退出登录" → 清 storage，跳启动页

### 5.8 腾讯会议接入设置页

```
┌────────────────────────────────────────┐
│ < 腾讯会议接入                          │
├────────────────────────────────────────┤
│ 配置个人 Token，让 Agent 自动拉取        │
│ 你的会议日程和已结束会议的智能纪要。      │
│                                        │
│ 1️⃣ 访问腾讯会议 AI Skill 页面           │
│    [打开 meeting.tencent.com/ai-skill]  │
│                                        │
│ 2️⃣ 复制你的 Token 粘贴在这里            │
│                                        │
│    Token: [________________________]    │
│                                        │
│        [测试连接]    [保存]             │
│                                        │
├────────────────────────────────────────┤
│ ⚠️ 重要提示                             │
│                                        │
│ 拉取智能纪要需要会议**开启云录制**。      │
│ 个人版默认未开启，请在每场需要纪要的       │
│ 会议中手动开启。                         │
│                                        │
│ 📖 [如何开启云录制 →]                   │
│    https://meeting.tencent.com/         │
│    support/topic/cloud-recording/      │
│                                        │
└────────────────────────────────────────┘
```

> "如何开启云录制"链接的 URL 待确认，可换成腾讯会议帮助中心搜索结果页或客服文章。

#### 行为
- `[测试连接]` → POST `/api/me/tencent/test { token }` → 后端调 MCP `convert_timestamp` 验证 → 返回 `{ ok: true/false, hint }`
- `[保存]` → PUT `/api/me/tencent { token }` → 后端 AES 加密存 `ir_users.tencent_meeting_token`

## 6. 后端接口缺口

| # | 接口 | 用途 | 工作量 |
|---|---|---|---|
| 1 | `GET /api/me` | 当前 IR 信息 | 0.2 天 |
| 2 | `PUT /api/me/tencent { token }` | 配置腾讯会议 token（AES 加密入库） | 0.5 天 |
| 3 | `POST /api/me/tencent/test { token }` | 验证 token 有效性 | 0.3 天 |
| 4 | `GET /api/me/tencent/meetings?status=ended&days=31` | 拉 IR 的腾讯会议列表 | 0.5 天 |
| 5 | `GET /api/investors/{id}/interactions?limit=5` | 投资人详情页用 | 0.3 天 |
| 6 | `GET /api/outreach/pending` | 待审核草稿列表（草稿历史页 + 对话首屏聚合） | 0.5 天 |
| 7 | `POST /api/agent/chat { message }` | 通用对话（自由问答） | 1 天 |
| 8 | 修改 `/api/agent/run`：增 `tencent_meeting_id` 字段 | 触发腾讯纪要拉取 | 0.5 天 |
| 9 | 新建 `services/tencent_meeting.py`（MCP 客户端封装） | 调腾讯 MCP | 1 天 |
| 10 | `meeting_minutes` workflow：增 `fetch_tencent_minutes` 节点 | 优先用腾讯纪要 | 0.5 天 |

**后端总工作量**：约 5.3 天

## 7. 数据库变更

```sql
ALTER TABLE ir_users
  ADD COLUMN tencent_meeting_token VARBINARY(255) NULL COMMENT 'AES-encrypted personal token';
```

新增表（草稿历史用）：
```sql
-- outreach_records 表已有，无需新增。
-- 但需要确认有 ir_id 字段以支持 GET /api/outreach/pending 按用户过滤。
```

## 8. 项目结构（小程序前端）

```
miniprogram/
├── app.js                    # 全局：登录、JWT 存取、WS 单例
├── app.json                  # tabBar 配置
├── app.wxss                  # 全局样式 + Agent 颜色变量
├── pages/
│   ├── splash/               # 启动页
│   ├── calendar/             # 日程 Tab（月视图）
│   ├── calendar-day/         # 日视图
│   ├── chat/                 # 对话 Tab
│   ├── investors/            # 投资人 Tab
│   ├── investor-detail/      # 投资人详情
│   ├── meeting-prepare/      # 会议纪要准备
│   ├── me/                   # 我
│   ├── tencent-setup/        # 腾讯会议接入
│   └── drafts/               # 草稿历史
├── components/
│   ├── agent-card/           # Agent 卡片组件（含按钮逻辑）
│   ├── thinking/             # 思考态组件
│   ├── review-modal/         # 审核 Modal
│   └── investor-tile/        # 投资人列表卡片
├── services/
│   ├── api.js                # 统一 fetch（带 JWT、401 重定向）
│   ├── ws.js                 # WebSocket 管理（单例 + 自动 close on done）
│   ├── upload.js             # Qiniu 上传封装
│   └── auth.js               # wx.login + token 管理
└── utils/
    ├── time.js               # 时间格式化
    └── storage.js
```

## 9. 技术决策

| 项 | 决策 | 理由 |
|---|---|---|
| **小程序框架** | 原生小程序（不用 Taro/uni-app） | 减少抽象层，对 WS/录音权限友好；MVP 体量不大 |
| **状态管理** | 简单 Page setData + 全局 app.globalData | MVP 不需要 redux 级别；2 处共享状态：JWT、当前 thread_id |
| **样式** | 不引入 UI 库，用 `wx-iconfont` + 自写组件 | 设计风格独特，UI 库会拖累 |
| **图标** | iconfont（在线引入） | 快 |
| **WebSocket** | `wx.connectSocket`，Authorization 通过 query string `?token=` | 小程序 header 不可控 |
| **本地存储** | `wx.setStorageSync` | JWT、onboarded 标记、最近 thread_id |

## 10. 开发优先级（MVP 拆分）

按依赖顺序：

| 阶段 | 工作 | 估时 |
|---|---|---|
| **后端 P0** | 接口 1-9（不含 tencent integration），数据库迁移 | 2 天 |
| **后端 P1** | tencent integration（接口 4 + service + workflow 节点） | 2 天 |
| **前端 P0** | app.json/tabBar、登录、对话 Tab、投资人 Tab、日程 Tab（不含腾讯拉取）| 4 天 |
| **前端 P1** | 会议纪要准备页（含腾讯拉取）、我页、腾讯设置页 | 2 天 |
| **联调 + 修复** | 端到端 4 个工作流跑通 | 2 天 |
| **总计** | | **~12 天** |

## 11. 待确认（TBD）

- **Q1**：自由对话 (`/api/agent/chat`) 是否需要保留对话上下文（多轮记忆）？MVP 推荐**不保留**（每次独立 LLM 调用），简化实现；如果体验差再加。
- **Q2**：「如何开启云录制」教程链接的具体 URL —— 走腾讯官方帮助中心 or 公司内部 SOP 文档？
- **Q3**：草稿历史页是否需要按 task_type 分组？还是按时间倒序简单列？**推荐**简单时间倒序 + 顶部 chip 筛选 task_type。
- **Q4**：投资人 `+ 新增` 按钮 —— MVP 完全不做？还是给个 admin-only 的最小实现？**推荐**MVP 完全不做，按钮隐藏，admin 用后台管理。
- **Q5**：日程月视图的 N 个月数据怎么聚合 —— 进入即拉当月所有日 (~30 个 /api/calendar/daily 请求 = 慢)？还是新增 `GET /api/calendar/month?month=YYYY-MM` 单个接口？**推荐后者**，需新增到接口缺口列表。

## 12. 验收标准

MVP 上线视为成功的标志：
- 5 名 IR 完成了至少一次完整流程（登录 → 触发 workflow → 审核 → 通过）
- 至少 3 名 IR 配置了腾讯会议接入并成功拉过一次纪要
- 主流程零阻塞性 bug（404/500/卡死）
- 后端 MySQL `outreach_records` 表至少 30 条 `status=approved` 的记录

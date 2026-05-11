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
- 多公司/多团队隔离 —— 单租户假设
- IR 自助导入投资人 —— 数据由后台导入或同步
- 投资人匹配/反馈归因/交易推进 Agent —— 阶段 2-3
- 现场录音 —— MVP 只支持文件上传
- **聊天气泡漫游** —— 仅本机本地存储，换手机/清缓存会丢失气泡历史；草稿和工作流结果在数据库有持久化，不影响业务

## 2. 用户角色

| 角色 | 权限 | 来源 |
|---|---|---|
| **IR**（投资人关系经理） | 使用所有 Agent 工作流、查看自己的草稿和投资人 | 管理员后台录入**姓名 + 手机号**（不是 openid） |
| **Admin** | IR 全部权限 + 管理 IR 用户 | 数据库 `role='admin'` |

### 登录绑定流程（修正）

`openid` 由微信在用户首次访问小程序时生成，admin **无法预先获知**。绑定走"手机号反向匹配"：

```
admin 后台 ──→ 录入 IR 姓名 + 手机号 ──→ ir_users (openid=NULL)
                                                  ▲
                                                  │ (回写)
                                                  │
小程序首次启动:                                    │
  wx.login() ──→ code ──→ POST /api/auth/login ─→ 后端拿 openid
       │                          │
       │                  if openid 已绑过用户:
       │                     直接返回 JWT
       │                  else:
       │                     返回 { need_phone_binding: true }
       │
       ▼
  [显示"请绑定手机号"按钮]
       │
       ▼
  wx.getPhoneNumber() ──→ encryptedData ──→ POST /api/auth/bind_phone
                                                  │
                                          后端解密拿手机号 ──→ 查 ir_users
                                                  │
                                              if matched: 写 openid，返回 JWT
                                              else:       返回 403 "账号未开通"
```

**前提**：必须使用**企业认证小程序**（个人主体不开放 `wx.getPhoneNumber`）。
申请前请确认公司有微信认证企业主体，没有的话 MVP 阻塞 ~5 工作日。

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

### 5.1 启动页 + 绑定流程

#### 启动页（仅首次）
- 全屏紫色渐变 + "FA" logo
- 标题：「你的 IR 工作台 / 由 4 个 Agent 协同完成」
- 副标题：「Orchestrator 每日为你规划日程，名单/内容/触达 Agent 主动推送草稿，你只需在对话中一键审核」
- 按钮：`进入工作台 →`

#### 行为流程
1. 点击 `进入工作台 →`
2. `wx.login()` 拿 code → POST `/api/auth/login { code }`
3. 分两种情况：
   - **已绑定** → 后端返回 `{ token, ir_id, name, role }` → 存 JWT → 跳日程 Tab
   - **未绑定** → 后端返回 `{ need_phone_binding: true, login_session: "xxx" }` → **进入手机号绑定页**

#### 手机号绑定页
```
┌────────────────────────────────────────┐
│   首次使用，请绑定手机号                  │
│                                        │
│   你的手机号需要和管理员录入的          │
│   一致才能进入系统。                    │
│                                        │
│   ┌──────────────────────────────┐    │
│   │  📱 微信授权获取手机号          │    │  ← <button open-type="getPhoneNumber">
│   └──────────────────────────────┘    │
│                                        │
│   找不到匹配的账号？                    │
│   联系管理员开通：admin@xxx.com         │
└────────────────────────────────────────┘
```

- 按钮触发 `wx.getPhoneNumber` → 拿到 `encryptedData` + `iv`
- POST `/api/auth/bind_phone { login_session, encryptedData, iv }`
- 后端用 `wx.business.getuserphonenumber` API 解密 → 查 `ir_users.phone` 匹配
  - 匹配 → 写 `openid`、返回 JWT → 跳日程 Tab
  - 不匹配 → 返回 403 + 提示文案
- 设置 `mro:onboarded=true`

### 5.2 日程 Tab

#### 月视图（默认进入）
- 顶部：标题「我的日程 ✦ AI 生成」+ 右上角头像
- 中部：月历（小程序原生 `<picker>` 或自绘）
  - 每个日期格子下方显示彩色圆点：`● 跟进 ● 会议 ● 里程碑 ● 推送`（4 色对应 `event.type`）
- 下方：当日事件预览列表（最多 3 条，更多展开）
- 数据：进入页面调 `GET /api/calendar/month?month=YYYY-MM`（**新增接口**），单次返回当月所有日聚合的事件 type 数组（用于点标签）+ 今日预览
- 切换月份重新调用，前端缓存当前月数据
- 点某天进入日视图时，再调 `GET /api/calendar/daily?target_date=YYYY-MM-DD` 拉详细事件

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
- 拉对话历史：本地 storage 存 `chat:history:{ir_id}`（最多 50 条，超出滚动加载）
- **首次/无历史**：注入一条 Orchestrator 早安卡（数据来自 `GET /api/calendar/daily` 的统计）
- WebSocket：如果 storage 里有 `current_thread_id`，尝试连 `wss://.../ws/{thread_id}?token=jwt`，连不上触发**重连机制**（见下）

#### WebSocket 重连机制（MVP 必须）
- `wx.onSocketClose` 触发 → 1 秒后重连一次（带退避：1s, 3s, 8s 三次后放弃）
- 重连失败 → 思考态卡片右上角显示 `[刷新]` 按钮
- 点 `[刷新]` → 调 `GET /api/agent/{thread_id}/state`（**新增接口**），拉当前快照
  - 返回 `{ status: "running" | "waiting_review" | "done" | "error", draft, final, error }`
  - 按 status 重渲染卡片（绕过 WS）
- 网络恢复后 `wx.onSocketOpen` → 自动重新订阅

#### 发送消息

自由对话和工作流触发走两条不同路径：

- **自由对话**（用户在底部输入框打字/发问）：POST `/api/agent/chat { message, history }`（**新增接口**，见接口缺口 #7）
  - `history`：前端维护**最近 10 条**消息（user + assistant 交替），每次请求带上
  - 后端单独的轻量 LLM 处理，不进入 4 个工作流，无 review
  - 用于：解释概念、查信息、闲聊、引导
- **工作流触发**（从卡片按钮、日程页跳转）：POST `/api/agent/run { task_type, ... }`
  - 显式 task_type，进入对应 LangGraph 工作流
  - 有 review 节点，会推送审核卡
  - 工作流不享受 `history` 上下文（每次独立任务）

> 这种分离避免了"前端猜 task_type"的复杂性。`history` 在自由对话中保留多轮上下文（如"帮我查张伟" → "他现在什么职级"），这是 chat-first 设计的基本预期。

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
│ │   ⚠️ 长于 5000 字建议改用音频     │  │
│ │      上传或在 PC 端粘贴          │  │
│ └──────────────────────────────────┘  │
│                                        │
└────────────────────────────────────────┘
```

> 粘贴入口主要给"几百字关键摘录"场景用。1 小时会议的几万字文字稿在手机上既难粘贴又易卡顿，UI 文案要明确引导用户走音频或 PC 端。

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

### 5.6 投资人 Tab（全 CRUD + 互动记录）

> **产品定位**：小程序是 IR 操作系统的主入口，IR 不应依赖 PC 端企名片。投资人/互动相关在小程序完成闭环。

#### 列表页
- 顶部：标题「投资人库」+ 右上角头像 + **`+ 新增`** 按钮（所有 IR 可见）
- 搜索框（query: `q`）
- 横向滚动标签条：A轮/B轮/C轮/医疗/AI/SaaS/...（多选，标签状态本地保存）
- 投资人卡片列表（按关系热度排序）
- 数据：`GET /api/investors?stage=&industry=&q=`
- 底部：`共 N 位投资人 · ✦ AI 已按关系热度排序`

#### 详情页（`/pages/investor/detail?id=X`）
- 头部：紫渐变 + 头像（首字符）+ 名字 + 机构·职务 + **右上角 `编辑` 按钮**
- 关系值：5 点（可点点修改 0-5）
- 标签 chips（点 `+` 加标签、点 chip 删）
- "AI 偏好画像"区：`profile_notes` 多段 `[MM-DD] 文字`
- "近期互动"区：从 `interaction_logs` 表拉，列出最近 5 条 + **`+ 记一条互动`** 按钮
- 底部 sticky 按钮组：
  - `✦ 问 Agent 关于 张伟`（左，主按钮）
  - `更多 ⋯`（右，菜单：编辑 / 删除）

#### 新增/编辑投资人页（`/pages/investor/edit?id=X`，id 为空时是新增）

```
┌────────────────────────────────────────┐
│ < 新增投资人              [保存]        │
├────────────────────────────────────────┤
│ * 姓名     [____________]              │
│   机构     [____________]              │
│   职务     [____________]              │
│   关系值   ● ● ● ○ ○ (点选 0-5)       │
│                                        │
│  联系方式                               │
│   手机     [____________]              │
│   微信     [____________]              │
│   邮箱     [____________]              │
│                                        │
│  偏好                                   │
│   行业标签 [A轮][消费][TMT]+           │
│   阶段     [A轮][B轮]+                 │
│   单笔     [____________]              │
│                                        │
│  纪念日                                 │
│   生日     [2026-05-11 >]              │
│   入职机构 [2020-01-10 >]              │
│                                        │
│  AI 偏好画像                            │
│   ┌────────────────────────────────┐  │
│   │ (多行文本，初始为空)            │  │
│   └────────────────────────────────┘  │
│                                        │
│        [删除投资人] (编辑模式才显示)    │
└────────────────────────────────────────┘
```

- 行业标签/阶段：点 `+` 弹底部 actionsheet 选预设标签
- 删除：弹二次确认 → DELETE `/api/investors/{id}`（**软删除**，写 `is_active=false`）
- 保存：POST/PUT `/api/investors`

#### 手动记互动页（`/pages/interaction/new?investor_id=X`）

```
┌────────────────────────────────────────┐
│ < 记一条互动              [保存]        │
├────────────────────────────────────────┤
│ 投资人  张伟（高榕资本）  [更换]        │
│                                        │
│ * 类型   [○微信 ○电话 ○见面 ○邮件 ○其他]│
│ * 时间   [2026-05-11 14:30 >]          │
│   时长   [__] 分钟（电话/见面）        │
│                                        │
│ * 内容/要点                             │
│   ┌────────────────────────────────┐  │
│   │ 聊了项目A的估值逻辑，他认为...    │  │
│   └────────────────────────────────┘  │
│                                        │
│   下次跟进时间                          │
│   [2026-05-25 >] [+ 加入日历]          │
└────────────────────────────────────────┘
```

保存后：
- POST `/api/investors/{id}/interactions { type, occurred_at, duration_min, summary, next_followup_at }`
- 后端写 `interaction_logs` 表 + 更新 investor 的 `last_interaction_at`
- 如果勾选"加入日历"，按 `next_followup_at` 在日历上显示一条 `followup` 类型事件

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
│    support/topic/1853/index.html        │
│                                        │
└────────────────────────────────────────┘
```

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
| 7 | `POST /api/agent/chat { message, history }` | 通用对话（自由问答 + 短期上下文） | 1 天 |
| 8 | 修改 `/api/agent/run`：增 `tencent_meeting_id` 字段 | 触发腾讯纪要拉取 | 0.5 天 |
| 9 | 新建 `services/tencent_meeting.py`（MCP 客户端封装） | 调腾讯 MCP | 1 天 |
| 10 | `meeting_minutes` workflow：增 `fetch_tencent_minutes` 节点 | 优先用腾讯纪要 | 0.5 天 |
| 11 | `GET /api/calendar/month?month=YYYY-MM` | 月历视图聚合（取代每日 30 次请求） | 0.5 天 |
| 12 | `GET /api/agent/{thread_id}/state` | WS 断线后拉当前快照（thinking → done/waiting_review/error） | 0.5 天 |
| 13 | 改造 `/api/auth/login` + 新增 `POST /api/auth/bind_phone` | 手机号反向匹配绑定流程 | 1 天 |
| 14 | `POST /api/investors` | 新增投资人 | 0.3 天 |
| 15 | `PUT /api/investors/{id}` | 编辑投资人 | 0.3 天 |
| 16 | `DELETE /api/investors/{id}` | 软删投资人（is_active=false） | 0.2 天 |
| 17 | `POST /api/investors/{id}/interactions` | 手动记一条互动 + 自动更新 last_interaction_at | 0.5 天 |
| 18 | 新建 `interaction_logs` 模型 + 迁移 | 互动记录持久化（含 type/duration/summary/next_followup） | 0.5 天 |

**后端总工作量**：约 9.1 天

## 7. 数据库变更

```sql
ALTER TABLE ir_users
  ADD COLUMN phone VARCHAR(20) NULL COMMENT '手机号，用于绑定时反向匹配',
  ADD COLUMN tencent_meeting_token VARBINARY(255) NULL COMMENT 'AES-encrypted personal token',
  ADD UNIQUE KEY uk_phone (phone),
  MODIFY COLUMN wechat_openid VARCHAR(64) NULL;  -- openid 现在变成可空，绑定后才有值
```

> 现有的 `/api/admin/users/{id}/bind` 接口失效，admin 后台改成只录手机号，openid 由小程序绑定流程自动写入。

```sql
-- outreach_records 表已有，无需新增。
-- 但需要确认有 ir_id 字段以支持 GET /api/outreach/pending 按用户过滤。
```

**新增 interaction_logs 表**：

```sql
CREATE TABLE interaction_logs (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  investor_id     INT NOT NULL,
  ir_id           INT NOT NULL COMMENT '哪个 IR 记的',
  type            VARCHAR(20) NOT NULL COMMENT 'wechat|phone|meeting|email|other',
  occurred_at     DATETIME NOT NULL COMMENT '互动发生时间',
  duration_min    SMALLINT NULL COMMENT '时长（分钟），电话/见面用',
  summary         TEXT NOT NULL COMMENT '要点摘要',
  next_followup_at DATETIME NULL COMMENT '建议下次跟进时间',
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_investor_time (investor_id, occurred_at DESC),
  INDEX idx_ir (ir_id),
  CONSTRAINT fk_il_investor FOREIGN KEY (investor_id) REFERENCES investors(id),
  CONSTRAINT fk_il_ir FOREIGN KEY (ir_id) REFERENCES ir_users(id)
);
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
│   ├── investor-edit/        # 新增/编辑投资人
│   ├── interaction-new/      # 手动记互动
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
| **后端 P0** | 接口 1, 5, 6, 7, 11, 12, 13 + DB 迁移 + 手机号绑定 | 3 天 |
| **后端 P1** | 接口 2, 3, 4, 8, 9, 10（腾讯会议集成） | 2 天 |
| **后端 P2** | 接口 14-18（投资人 CRUD + 互动记录） + interaction_logs 表 | 2 天 |
| **前端 P0** | app.json/tabBar、登录+绑定流程、对话 Tab（含 WS 重连）、投资人 Tab（只读）、日程 Tab | 4.5 天 |
| **前端 P1** | 会议纪要准备页（含腾讯拉取）、我页、腾讯设置页、长内容审核 Modal | 2.5 天 |
| **前端 P2** | 投资人新增/编辑/删除、手动记互动 | 1.5 天 |
| **联调 + 修复** | 端到端 4 个工作流 + 投资人 CRUD + 移动端弱网测试 | 2 天 |
| **总计** | | **~17.5 天** |

> **企业认证小程序申请**已在走流程，前期开发用测试版联调。
> **加 admin 录入 IR 用户**：MVP 不做 admin 后台，admin 直接 curl 调 `POST /api/admin/users` 加 IR（用户数 <20 不需要专门工具）。

## 11. 待确认（TBD）

无未决问题。所有原 Q1-Q5 已解决：

| 原问题 | 落地 |
|---|---|
| ~~Q1 云录制教程链接~~ | 用 `meeting.tencent.com/support/topic/1853/index.html` |
| ~~Q2 草稿历史排序~~ | 时间倒序 + chip 筛选 task_type |
| ~~Q3 新增投资人~~ | 全 CRUD（新增/编辑/软删 + 手动记互动），见 5.6 |
| ~~Q4 企业认证小程序~~ | 已在申请，开发期用测试版联调 |
| ~~Q5 admin 后台~~ | MVP 不做，admin 直接 curl `POST /api/admin/users`（IR <20 人） |
| ~~自由对话上下文~~ | 前端维护 10 条 history |
| ~~月视图聚合接口~~ | 接口缺口 #11 |
| ~~WS 重连机制~~ | 接口缺口 #12 + 5.3 重连逻辑 |

## 12. 验收标准

MVP 上线视为成功的标志：
- 5 名 IR 完成了至少一次完整流程（登录 → 触发 workflow → 审核 → 通过）
- 至少 3 名 IR 配置了腾讯会议接入并成功拉过一次纪要
- 主流程零阻塞性 bug（404/500/卡死）
- 后端 MySQL `outreach_records` 表至少 30 条 `status=approved` 的记录

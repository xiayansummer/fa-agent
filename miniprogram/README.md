# FA Agent 微信小程序

## 开发环境

- 微信开发者工具 ≥ 1.06
- TypeScript 5.x

## 启动

1. 打开微信开发者工具
2. 导入项目 → 选择 `miniprogram/` 目录
3. AppID 填测试号或公司企业版 AppID（替换 `project.config.json` 里的 `wxTESTAPPID000000000`）
4. 编译运行

## 项目结构

- `pages/` 页面
- `utils/` 工具函数（storage, time）
- `services/` API/WS 调用层（F1b/c 添加）
- `components/` 复用组件（F4 添加）

## Services 层

`services/` 目录封装所有网络调用，页面层不直接使用 `wx.request`。

### `services/api.ts`

统一 HTTP 请求封装：

- **自动 JWT 注入**：从 storage 读取 `mro:jwt`，附加 `Authorization: Bearer <token>` header。
- **401 处理**：清除 `mro:jwt` → 弹 toast "登录已过期" → `wx.reLaunch` 到 splash 页。
- **403 处理**：弹 toast 显示 `detail` 或"无权限"。
- **4xx 业务错误**：弹 toast 显示 `detail` 或"请求参数错误"。
- **5xx 服务端错误**：弹 toast "服务器繁忙"。
- **网络失败**：弹 toast "网络异常，请重试"。
- **`silent` 参数**：传 `{ silent: true }` 跳过所有 toast（适用于轮询等场景）。

便捷方法：`api.get / api.post / api.put / api.del`

### `services/auth.ts`

认证相关操作：

| 函数 | 说明 |
|------|------|
| `login()` | `wx.login` → `POST /api/auth/login`；已绑返回 `LoginResponse`，未绑返回 `NeedBindingResponse` |
| `bindPhone(session, encryptedData, iv)` | `POST /api/auth/bind_phone`，完成绑定后存 jwt + user |
| `logout()` | 清 storage → `wx.reLaunch` splash |
| `getCurrentUser(forceFresh?)` | 带模块级缓存；`forceFresh=true` 强制重新请求 `/api/me` |
| `clearCachedUser()` | 清内存缓存（登出时自动不需调用，logout 会 reLaunch） |

### WebSocket（services/ws.ts）

订阅 Agent 工作流事件流：

```typescript
import { wsManager } from './services/ws';

wsManager.subscribe(thread_id, (event) => {
  switch (event.type) {
    case 'node_done': // ...
    case 'waiting_review': // ...
    case 'done': // ...
    case 'error': // ...
    case 'snapshot': // 重连失败后拉的快照
  }
});

// 离开页面：
wsManager.unsubscribe(thread_id);
```

重连策略：1s/3s/8s 退避，3 次失败后调 `/state` 拉快照。
`done` 或 `error` 事件会主动断开，不重连。

## 当前状态

- F1a ✅ 脚手架完成
- F1b ✅ API/Auth services 完成
- F1c ✅ WS service 完成
- F2 ✅ splash 启动页 + 手机号绑定页完成
- F3 ✅ 日程 Tab（月视图 + 日视图）完成
- F4a ✅ 对话 Tab 骨架 + agent-card + thinking 组件完成
- F4b ✅ 对话 Tab WS 集成 + Orchestrator 早安卡完成
- F4c ✅ 短内容内联审核（inlineEditable + review POST + IrAction 映射）完成
- F5-F10 待实现 (业务页)

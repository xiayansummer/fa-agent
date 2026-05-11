# MVP 联调与发布清单

## 一、后端部署（一次性）

1. [ ] 拉最新 master 到本地：`git pull origin master`
2. [ ] 跑部署脚本：`bash scripts/deploy_mvp.sh`
3. [ ] 在服务器手动执行 deploy 脚本里提示的远程命令
4. [ ] 生成的 `TOKEN_ENCRYPT_KEY` **立刻备份到 1Password**（丢失则所有 IR 的腾讯 token 失效）
5. [ ] 验证 `https://agentapi.investarget.com/health` 返回 `{"status":"ok"}`
6. [ ] 验证 `https://agentapi.investarget.com/openapi.json` 列出全部新接口（应有 30+ endpoint）

## 二、注入测试数据（可选）

7. [ ] 在服务器跑 `python /root/fa-agent/scripts/seed_mvp.py`
8. [ ] 验证：DB 有 5 个测试 IR (`SELECT * FROM ir_users WHERE phone LIKE '138%'`)

## 三、生产 IR 录入

9. [ ] admin 用 curl 录入真实 IR 用户（一次性）：
```bash
TOKEN=$(curl -s -X POST https://agentapi.investarget.com/api/auth/login \
  -d '{"code":"<admin的openid对应code>"}' | jq -r .token)
curl -X POST https://agentapi.investarget.com/api/admin/users \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"name":"张三","phone":"13800000001","role":"ir"}'
```

## 四、小程序前端导入

10. [ ] 打开微信开发者工具
11. [ ] 导入项目 → 目录 `/Users/summer/fa-agent/miniprogram/`
12. [ ] 替换 `project.config.json` 的 AppID 为公司测试版/正式版 AppID
13. [ ] 编译运行，确认无 console 错误

## 五、联调脚本（必须按顺序）

### 5.1 登录流程
- [ ] 真机扫码进入小程序
- [ ] 点"进入工作台"
- [ ] 首次：跳手机号绑定页 → 微信授权 → 输入和管理员录入一致的手机号 → 进入日程 Tab
- [ ] 已登录：直接进日程 Tab

### 5.2 日程 Tab
- [ ] 月历显示当月，今日高亮
- [ ] 切月按钮可用
- [ ] 点某日 → 跳日详情，显示当日事件卡片

### 5.3 对话 Tab
- [ ] 进入显示 Orchestrator 早安卡
- [ ] 输入框打字 → 发送 → 收到 AI 回复（自由对话）
- [ ] 从日程页点"执行" → 切回对话 Tab → 看到工作流 thinking 卡 → waiting_review 卡 → 审核按钮

### 5.4 投资人 Tab
- [ ] 列表显示，搜索/标签筛选有效
- [ ] 点投资人 → 详情页，含偏好画像、近期互动、关系值
- [ ] "+ 新增" → 表单页 → 填写 → 保存 → 回列表
- [ ] 详情页 "编辑" → 修改 → 保存
- [ ] 详情页 "删除"（编辑页内） → 二次确认 → 回列表，已删除项不再显示

### 5.5 互动记录
- [ ] 详情页 "+ 记互动" → 选类型/时间/内容 → 保存 → 详情页"近期互动"显示新条目

### 5.6 我 / 腾讯会议
- [ ] 任一 Tab 右上头像 → 我页
- [ ] 显示个人信息 + tencent 状态（未配置）
- [ ] 点 "腾讯会议接入" → 设置页
- [ ] 复制 token 到输入框 → 点 "测试连接" → 显示 ✓
- [ ] 点 "保存" → 返回 → 我页显示"已配置"

### 5.7 会议纪要（3 模式）
- [ ] 日程页点 "纪要准备"（meeting 类型卡）→ 会议纪要准备页
- [ ] 模式 1：点 "从腾讯会议拉取" → actionsheet 显示已结束会议
  - 选有录制的 → 跳对话 Tab → 看到 Agent 处理 → 出审核卡
  - 选无录制的 → 提示拒绝
- [ ] 模式 2：点 "上传音频文件" → 选 mp3 → 上传 → 跳对话 → Agent 处理
- [ ] 模式 3：点 "粘贴文字稿" → 展开输入 → 提交 → 跳对话 → Agent 处理

### 5.8 审核（4 种结果）
- [ ] 短内容（<200 字）：点 "调整" → 卡片内联展开 → 编辑 → 提交 → 卡片变 "已通过"
- [ ] 长内容（>200 字）：点 "调整" → 弹全屏 Modal → 编辑 → "通过并发送" → 卡片变 "已通过"
- [ ] 点 "拒绝" → 卡片变 "已拒绝"
- [ ] 点 "通过" → POST review → 后端 save_node → 卡片变 "已通过"

### 5.9 草稿历史
- [ ] 我页 → "我的草稿历史"
- [ ] 显示按时间倒序的草稿列表
- [ ] chip 筛选 task_type 有效
- [ ] 点条目 → 弹 modal 显示全文

## 六、弱网测试

- [ ] 微信开发者工具 → 网络 → 切到 "4G/3G/弱网"
- [ ] 在对话页触发工作流 → 主动断开 → 看是否能重连
- [ ] WS 重连失败后 (3 次)，看是否会调 /state 拉快照并渲染

## 七、验收标准

- [ ] 5 名内测 IR 完成至少 1 个完整 workflow
- [ ] 数据库 `SELECT COUNT(*) FROM outreach_records WHERE status='approved'` ≥ 30
- [ ] 0 个 P0 bug（卡死/数据丢失/鉴权绕过）
- [ ] 弱网场景验证通过

## 八、已知限制（写入用户文档）

- 聊天气泡仅本机存储，换手机/清缓存会丢
- 推送通知未实现（IR 需主动打开小程序看待办）
- 长文字稿粘贴超过 5000 字建议改用音频或 PC 端
- 腾讯会议拉取需要会议**已开云录制**（个人版默认未开）

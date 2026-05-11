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

## 当前状态

- F1a ✅ 脚手架完成
- F1b/c 待实现 (api/ws services)
- F2 待实现 (登录 + 绑定页)
- F3-F10 待实现 (业务页)

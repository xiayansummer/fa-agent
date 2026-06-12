/**
 * 订阅消息攒配额：在用户手势（保存日程 / 点发送）里调用。
 * 平台规则：一次「允许」= 攒 1 条推送配额；用户勾过"总是保持以上选择"后
 * 不再弹窗、静默通过。accept 时上报后端 +1 配额，后端发提醒时扣减。
 * 全程 fire-and-forget，不阻塞主流程、失败静默。
 */
import { api } from '../services/api';

export const SCHEDULE_TMPL_ID = 'J1qIizvW8rJkrD4CZfKK6ldUcuOB3E6kiERojkOYXjU';

export function bankScheduleSubscribe(): void {
  try {
    wx.requestSubscribeMessage({
      tmplIds: [SCHEDULE_TMPL_ID],
      success: (res: any) => {
        if (res[SCHEDULE_TMPL_ID] === 'accept') {
          api.post('/api/me/subscribe', {}, { silent: true } as any).catch(() => {});
        }
      },
      fail: () => {/* 非手势上下文/开发者工具差异等，静默 */},
    });
  } catch (_e) {/* 老基础库无此 API，静默 */}
}

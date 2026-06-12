/**
 * 订阅消息攒配额：在用户手势（保存日程 / 点发送）里调用。
 * 平台规则：一次「允许」= 攒 1 条推送配额；用户勾过"总是保持以上选择"后
 * 不再弹窗、静默通过。accept 时上报后端 +1 配额，后端发提醒时扣减。
 *
 * ⚠️ 必须 await：返回 Promise，弹窗期间调用方流程要停住——否则后续的
 * navigateBack/页面跳转会把授权弹窗直接杀掉（"一闪而过点不到"）。
 * 勾过"总是保持"后 complete 立即回调，await 无感。
 */
import { api } from '../services/api';

export const SCHEDULE_TMPL_ID = 'J1qIizvW8rJkrD4CZfKK6ldUcuOB3E6kiERojkOYXjU';

export function bankScheduleSubscribe(): Promise<void> {
  return new Promise((resolve) => {
    try {
      wx.requestSubscribeMessage({
        tmplIds: [SCHEDULE_TMPL_ID],
        success: (res: any) => {
          if (res[SCHEDULE_TMPL_ID] === 'accept') {
            api.post('/api/me/subscribe', {}, { silent: true } as any).catch(() => {});
          }
        },
        complete: () => resolve(),   // 允许/拒绝/出错都放行主流程
      });
    } catch (_e) {
      resolve();  // 老基础库无此 API
    }
  });
}

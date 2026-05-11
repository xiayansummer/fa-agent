import { login, type NeedBindingResponse } from '../../services/auth';
import * as storage from '../../utils/storage';

Page({
  data: {
    loading: false,
    error: '',
  },

  onLoad() {
    // 已登录直接跳过
    const jwt = storage.get<string>('mro:jwt');
    if (jwt) {
      wx.switchTab({ url: '/pages/calendar/index' });
    }
  },

  async onEnter() {
    if (this.data.loading) return;
    this.setData({ loading: true, error: '' });

    try {
      const result = await login();
      if ('token' in result) {
        // 已绑定 → 主页
        storage.set('mro:onboarded', true);
        wx.switchTab({ url: '/pages/calendar/index' });
      } else {
        // 未绑定 → 跳绑定页
        const needBinding = result as NeedBindingResponse;
        wx.navigateTo({
          url: `/pages/bind-phone/index?login_session=${needBinding.login_session}`,
        });
      }
    } catch (e: any) {
      this.setData({ error: e?.detail || '登录失败，请重试' });
    } finally {
      this.setData({ loading: false });
    }
  },
});

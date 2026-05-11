import { bindPhone } from '../../services/auth';
import * as storage from '../../utils/storage';

interface PageData {
  loginSession: string;
  loading: boolean;
  error: string;
}

interface PhoneNumberResult {
  encryptedData: string;
  iv: string;
  errMsg?: string;
}

Page<PageData, {}>({
  data: {
    loginSession: '',
    loading: false,
    error: '',
  },

  onLoad(options: { login_session?: string }) {
    if (!options.login_session) {
      wx.reLaunch({ url: '/pages/splash/index' });
      return;
    }
    this.setData({ loginSession: options.login_session });
  },

  async onGetPhone(e: WechatMiniprogram.CustomEvent) {
    const detail = e.detail as PhoneNumberResult;

    // 用户拒绝授权 errMsg 含 "deny"
    if (!detail.encryptedData || !detail.iv) {
      this.setData({ error: '需要授权获取手机号才能继续' });
      return;
    }

    this.setData({ loading: true, error: '' });
    try {
      await bindPhone(this.data.loginSession, detail.encryptedData, detail.iv);
      storage.set('mro:onboarded', true);
      wx.switchTab({ url: '/pages/calendar/index' });
    } catch (e: any) {
      const detail = e?.detail || '绑定失败，请重试';
      // 403 是账号未开通，提示更友好
      if (e?.code === 403) {
        this.setData({ error: '该手机号未开通账号，请联系管理员' });
      } else if (e?.code === 410) {
        this.setData({ error: '会话已过期，请返回重新登录', loading: false });
        setTimeout(() => wx.reLaunch({ url: '/pages/splash/index' }), 2000);
        return;
      } else {
        this.setData({ error: detail });
      }
    } finally {
      this.setData({ loading: false });
    }
  },
});

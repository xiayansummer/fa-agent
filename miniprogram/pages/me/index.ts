import { getCurrentUser, logout, type CurrentUser } from '../../services/auth';

interface PageData {
  user: CurrentUser | null;
  loading: boolean;
}

Page<PageData, {}>({
  data: {
    user: null,
    loading: false,
  },

  onLoad() {
    this._load();
  },

  onShow() {
    this._load();  // tencent_bound 状态可能改了
  },

  async _load() {
    this.setData({ loading: true });
    try {
      const user = await getCurrentUser(true);  // 强制刷新
      this.setData({ user });
    } catch (e) {/* toast */} finally {
      this.setData({ loading: false });
    }
  },

  goTencentSetup() {
    wx.navigateTo({ url: '/pages/tencent-setup/index' });
  },

  goDrafts() {
    wx.navigateTo({ url: '/pages/drafts/index' });
  },

  async onLogout() {
    const ok = await new Promise<boolean>(resolve => {
      wx.showModal({
        title: '退出登录？',
        confirmText: '退出',
        confirmColor: '#DC2626',
        success: r => resolve(r.confirm),
      });
    });
    if (ok) logout();
  },
});

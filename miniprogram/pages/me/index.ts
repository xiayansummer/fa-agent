import { api } from '../../services/api';
import { getCurrentUser, logout, clearCachedUser } from '../../services/auth';

interface PageData {
  user: any;
  loading: boolean;
  nameEdit: string;
  qmingpianEdit: string;
  savingName: boolean;
  savingQmingpian: boolean;
  tencentTokenEdit: string;
  tencentTesting: boolean;
  tencentSaving: boolean;
  tencentTestResult: string;
  tencentTestOk: boolean;
}

Page<PageData, {}>({
  data: {
    user: null,
    loading: false,
    nameEdit: '',
    qmingpianEdit: '',
    savingName: false,
    savingQmingpian: false,
    tencentTokenEdit: '',
    tencentTesting: false,
    tencentSaving: false,
    tencentTestResult: '',
    tencentTestOk: false,
  },

  onLoad() {
    this._load();
  },

  onShow() {
    this._load();
  },

  async _load() {
    this.setData({ loading: true });
    try {
      const user = await getCurrentUser(true);
      this.setData({
        user,
        nameEdit: user.name || '',
        qmingpianEdit: user.qmingpian_username || '',
      });
    } catch (e) {/* toast 由 api 处理 */} finally {
      this.setData({ loading: false });
    }
  },

  onNameInput(e: WechatMiniprogram.Input) {
    this.setData({ nameEdit: e.detail.value });
  },

  async onSaveName() {
    if (!this.data.nameEdit.trim()) {
      wx.showToast({ title: '姓名不能为空', icon: 'none' });
      return;
    }
    if (this.data.nameEdit === this.data.user?.name) return;
    this.setData({ savingName: true });
    try {
      await api.put('/api/me', { name: this.data.nameEdit.trim() });
      wx.showToast({ title: '已保存', icon: 'success' });
      clearCachedUser();
      this._load();
    } catch (e) {/* toast */} finally {
      this.setData({ savingName: false });
    }
  },

  onQmingpianInput(e: WechatMiniprogram.Input) {
    this.setData({ qmingpianEdit: e.detail.value });
  },

  async onSaveQmingpian() {
    if (this.data.qmingpianEdit === (this.data.user?.qmingpian_username || '')) return;
    this.setData({ savingQmingpian: true });
    try {
      await api.put('/api/me', { qmingpian_username: this.data.qmingpianEdit.trim() });
      wx.showToast({ title: '已保存', icon: 'success' });
      clearCachedUser();
      this._load();
    } catch (e) {/* toast */} finally {
      this.setData({ savingQmingpian: false });
    }
  },

  onTencentTokenInput(e: WechatMiniprogram.Input) {
    this.setData({ tencentTokenEdit: e.detail.value, tencentTestResult: '', tencentTestOk: false });
  },

  copyAiSkillUrl() {
    wx.setClipboardData({
      data: 'https://meeting.tencent.com/ai-skill.html',
      success: () => wx.showToast({ title: '已复制，可粘贴到浏览器打开', icon: 'none' }),
    });
  },

  copyCloudRecordingUrl() {
    wx.setClipboardData({
      data: 'https://meeting.tencent.com/support/topic/1853/index.html',
      success: () => wx.showToast({ title: '已复制，可粘贴到浏览器打开', icon: 'none' }),
    });
  },

  async onTencentTest() {
    if (!this.data.tencentTokenEdit.trim()) {
      wx.showToast({ title: '请先填 token', icon: 'none' });
      return;
    }
    this.setData({ tencentTesting: true, tencentTestResult: '' });
    try {
      const res = await api.post<{ ok: boolean; detail?: string }>(
        '/api/me/tencent/test',
        { token: this.data.tencentTokenEdit.trim() },
        { silent: true }
      );
      this.setData({
        tencentTestOk: res.ok,
        tencentTestResult: res.ok ? '✓ token 可用' : `✗ ${res.detail || 'token 无效'}`,
      });
    } catch (e: any) {
      this.setData({ tencentTestOk: false, tencentTestResult: `✗ ${e?.detail || '测试失败'}` });
    } finally {
      this.setData({ tencentTesting: false });
    }
  },

  async onTencentSave() {
    if (!this.data.tencentTokenEdit.trim()) {
      wx.showToast({ title: '请先填 token', icon: 'none' });
      return;
    }
    this.setData({ tencentSaving: true });
    try {
      await api.put('/api/me/tencent', { token: this.data.tencentTokenEdit.trim() });
      wx.showToast({ title: '已保存', icon: 'success' });
      this.setData({ tencentTokenEdit: '', tencentTestResult: '' });
      this._load();
    } catch (e) {/* toast */} finally {
      this.setData({ tencentSaving: false });
    }
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

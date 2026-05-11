import { api } from '../../services/api';

interface PageData {
  token: string;
  testing: boolean;
  saving: boolean;
  testResult: string;
  testOk: boolean;
}

Page<PageData, {}>({
  data: {
    token: '',
    testing: false,
    saving: false,
    testResult: '',
    testOk: false,
  },

  onTokenInput(e: WechatMiniprogram.Input) {
    this.setData({ token: e.detail.value, testResult: '', testOk: false });
  },

  openAiSkill() {
    // 小程序无法直接打开外链，复制 URL
    wx.setClipboardData({
      data: 'https://meeting.tencent.com/ai-skill',
      success: () => wx.showToast({ title: 'URL 已复制', icon: 'success' }),
    });
  },

  openCloudRecordingDoc() {
    wx.setClipboardData({
      data: 'https://meeting.tencent.com/support/topic/1853/index.html',
      success: () => wx.showToast({ title: 'URL 已复制', icon: 'success' }),
    });
  },

  async onTest() {
    if (!this.data.token.trim()) {
      wx.showToast({ title: '请先填 token', icon: 'none' });
      return;
    }
    this.setData({ testing: true, testResult: '' });
    try {
      const res = await api.post<{ ok: boolean; detail?: string }>('/api/me/tencent/test', {
        token: this.data.token.trim(),
      }, { silent: true });
      this.setData({
        testOk: res.ok,
        testResult: res.ok ? '✓ token 可用' : `✗ ${res.detail || 'token 无效'}`,
      });
    } catch (e: any) {
      this.setData({ testOk: false, testResult: `✗ ${e?.detail || '测试失败'}` });
    } finally {
      this.setData({ testing: false });
    }
  },

  async onSave() {
    if (!this.data.token.trim()) {
      wx.showToast({ title: '请先填 token', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    try {
      await api.put('/api/me/tencent', { token: this.data.token.trim() });
      wx.showToast({ title: '已保存', icon: 'success' });
      setTimeout(() => wx.navigateBack(), 800);
    } catch (e) {/* api toast */} finally {
      this.setData({ saving: false });
    }
  },
});

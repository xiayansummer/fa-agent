import { api } from '../../services/api';

interface TencentMeeting {
  meeting_id: string;
  subject: string;
  start_time: string;
  end_time: string;
  has_recording: boolean;
}

interface PageData {
  investorIds: number[];     // 关联的投资人 id 列表（从 query 传入）
  showPaste: boolean;
  pasteText: string;
  loading: boolean;
  loadingTencent: boolean;
  /** 从日历点过来携带的会议 ID，未拿到录制时保留供 banner 显示 */
  pendingMeetingId: string;
  /** 已确认该会议无云录制 */
  noRecording: boolean;
}

Page<PageData, {}>({
  data: {
    investorIds: [],
    showPaste: false,
    pasteText: '',
    loading: false,
    loadingTencent: false,
    pendingMeetingId: '',
    noRecording: false,
  },

  onLoad(opts: { investor_id?: string; investor_ids?: string; meeting_id?: string }) {
    let ids: number[] = [];
    if (opts.investor_id) ids = [parseInt(opts.investor_id)];
    if (opts.investor_ids) ids = opts.investor_ids.split(',').map(Number).filter(Boolean);
    this.setData({ investorIds: ids });
    if (opts.meeting_id) {
      const mid = decodeURIComponent(opts.meeting_id);
      this.setData({ pendingMeetingId: mid });
      setTimeout(() => this._probeAndRun(mid), 50);
    }
  },

  /** 带 meeting_id 进入页面：先探测云录制 → 有则自动发起腾讯纪要；无则显示 banner 留在本页让用户选其他模式。 */
  async _probeAndRun(meetingId: string) {
    wx.showLoading({ title: '检查云录制...', mask: true });
    try {
      const res = await api.get<{ has_recording: boolean }>(
        `/api/me/tencent/meetings/${encodeURIComponent(meetingId)}/records`,
        { silent: true },
      );
      wx.hideLoading();
      if (res && res.has_recording) {
        await this._runWithTencent(meetingId);
      } else {
        this.setData({ noRecording: true });
        wx.showToast({ title: '此会议无云录制', icon: 'none', duration: 1500 });
      }
    } catch (e) {
      wx.hideLoading();
      // 探测失败也降级为"无录制"，提示用户用其他模式
      this.setData({ noRecording: true });
    }
  },

  // === 模式 1：腾讯会议 ===
  async onTencentTap() {
    if (this.data.loadingTencent) return;
    this.setData({ loadingTencent: true });
    try {
      const data = await api.get<{ meetings: TencentMeeting[] }>('/api/me/tencent/meetings?status=ended&days=31');
      const meetings = data.meetings || [];

      if (meetings.length === 0) {
        wx.showModal({
          title: '没有可用会议',
          content: '过去 31 天没有已结束的腾讯会议，或会议未开云录制。',
          showCancel: false,
        });
        return;
      }

      // actionsheet：每场会议一项（带 has_recording 标识）
      const items = meetings.map(m =>
        `${m.subject.slice(0, 20)} · ${m.start_time.slice(0, 10)}${m.has_recording ? ' ✓' : ' (无录制)'}`
      );
      wx.showActionSheet({
        itemList: items.slice(0, 6),  // wechat 限制 6 条
        success: async (res) => {
          const m = meetings[res.tapIndex];
          if (!m.has_recording) {
            wx.showModal({
              title: '该会议未开云录制',
              content: '请改用上传音频或粘贴文字稿模式。',
              showCancel: false,
            });
            return;
          }
          await this._runWithTencent(m.meeting_id);
        },
      });
    } catch (e: any) {
      if (e?.code === 422) {
        wx.showModal({
          title: '请先配置腾讯会议',
          content: '前往「我」页面 → 腾讯会议接入 配置 token',
          confirmText: '去配置',
          success: r => r.confirm && wx.navigateTo({ url: '/pages/tencent-setup/index' }),
        });
      }
    } finally {
      this.setData({ loadingTencent: false });
    }
  },

  async _runWithTencent(tencentMeetingId: string) {
    if (!(await this._confirmUnboundIfNeeded())) return;
    this.setData({ loading: true });
    try {
      const res = await api.post<{ thread_id: string }>('/api/agent/run', {
        task_type: 'meeting_minutes',
        tencent_meeting_id: tencentMeetingId,
        investor_ids: this.data.investorIds,
      });
      this._goChat(res.thread_id);
    } catch (e) {/* toast handled by api layer */} finally {
      this.setData({ loading: false });
    }
  },

  // === 模式 2：上传音频 ===
  async onUploadTap() {
    if (this.data.loading) return;
    if (!(await this._confirmUnboundIfNeeded())) return;

    const fileRes = await new Promise<any>((resolve) => {
      wx.chooseMessageFile({
        count: 1,
        type: 'file',
        extension: ['mp3', 'm4a', 'wav', 'aac'],
        success: r => resolve(r.tempFiles[0]),
        fail: () => resolve(null),
      });
    });
    if (!fileRes) return;

    if (fileRes.size > 200 * 1024 * 1024) {
      wx.showToast({ title: '文件超过 200MB', icon: 'none' });
      return;
    }

    this.setData({ loading: true });
    wx.showLoading({ title: '上传中...' });

    try {
      // 1. 拿 token
      const tokenRes = await api.post<{
        token: string; key: string; upload_url: string;
      }>('/api/upload/token', {
        purpose: 'audio',
        filename: fileRes.name,
      });

      // 2. wx.uploadFile 直传
      await new Promise<void>((resolve, reject) => {
        wx.uploadFile({
          url: tokenRes.upload_url,
          filePath: fileRes.path,
          name: 'file',
          formData: { token: tokenRes.token, key: tokenRes.key },
          success: r => r.statusCode === 200 ? resolve() : reject(new Error(`upload ${r.statusCode}`)),
          fail: (err) => reject(new Error(err.errMsg || 'upload failed')),
        });
      });

      // 3. 拿签名 URL
      const signRes = await api.get<{ url: string }>(
        `/api/upload/sign?key=${encodeURIComponent(tokenRes.key)}&expires=3600`
      );

      // 4. 触发 workflow
      const runRes = await api.post<{ thread_id: string }>('/api/agent/run', {
        task_type: 'meeting_minutes',
        audio_url: signRes.url,
        investor_ids: this.data.investorIds,
      });

      wx.hideLoading();
      this._goChat(runRes.thread_id);
    } catch (e: any) {
      wx.hideLoading();
      wx.showToast({ title: e?.message || '上传失败', icon: 'none' });
    } finally {
      this.setData({ loading: false });
    }
  },

  // === 模式 3：粘贴文字 ===
  togglePaste() {
    this.setData({ showPaste: !this.data.showPaste });
  },

  onPasteInput(e: WechatMiniprogram.Input) {
    this.setData({ pasteText: e.detail.value });
  },

  async onSubmitPaste() {
    const text = this.data.pasteText.trim();
    if (!text) {
      wx.showToast({ title: '请粘贴文字稿', icon: 'none' });
      return;
    }
    if (!(await this._confirmUnboundIfNeeded())) return;

    this.setData({ loading: true });
    try {
      const res = await api.post<{ thread_id: string }>('/api/agent/run', {
        task_type: 'meeting_minutes',
        transcript: text,
        investor_ids: this.data.investorIds,
      });
      this._goChat(res.thread_id);
    } catch (e) {/* toast handled by api layer */} finally {
      this.setData({ loading: false });
    }
  },

  /** 启动 workflow 前的软提示：没关联投资人就弹确认。已选则直接 true。 */
  async _confirmUnboundIfNeeded(): Promise<boolean> {
    if ((this.data.investorIds || []).length > 0) return true;
    const res = await new Promise<{ confirm: boolean }>((resolve) => {
      wx.showModal({
        title: '未关联投资人',
        content: '本次纪要将归档到「无关联」类，可在「我的草稿历史」找到，但不会自动写入投资人互动。是否继续？',
        confirmText: '继续',
        cancelText: '返回',
        success: (r) => resolve({ confirm: !!r.confirm }),
        fail: () => resolve({ confirm: false }),
      });
    });
    return res.confirm;
  },

  _goChat(threadId: string) {
    wx.setStorageSync('chat:incoming_thread', threadId);
    wx.switchTab({ url: '/pages/chat/index' });
  },
});

import { api } from '../../services/api';
import { formatDate } from '../../utils/time';

const TYPE_OPTIONS = [
  { value: 'meeting', label: '见面' },
  { value: 'phone', label: '电话' },
  { value: 'wechat', label: '微信' },
  { value: 'email', label: '邮件' },
  { value: 'other', label: '其他' },
];

interface PageData {
  investorId: number;
  investorName: string;
  type: string;
  date: string;        // YYYY-MM-DD
  time: string;        // HH:mm
  duration: string;    // 分钟（字符串，便于输入）
  summary: string;
  followupDate: string;
  saving: boolean;
  showDuration: boolean;
  typeOptions: typeof TYPE_OPTIONS;
}

Page<PageData, {}>({
  data: {
    investorId: 0,
    investorName: '',
    type: 'meeting',
    date: '',
    time: '',
    duration: '',
    summary: '',
    followupDate: '',
    saving: false,
    showDuration: true,
    typeOptions: TYPE_OPTIONS,
  },

  onLoad(opts: { investor_id?: string }) {
    if (!opts.investor_id) {
      wx.showToast({ title: '缺少投资人', icon: 'none' });
      setTimeout(() => wx.navigateBack(), 1000);
      return;
    }
    const id = parseInt(opts.investor_id);
    const now = new Date();
    this.setData({
      investorId: id,
      date: formatDate(now),
      time: `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`,
    });
    this._loadInvestorName(id);
  },

  async _loadInvestorName(id: number) {
    try {
      const inv = await api.get<{ name: string }>(`/api/investors/${id}`, { silent: true });
      this.setData({ investorName: inv.name });
    } catch (e) {/* silent */}
  },

  onTypeTap(e: WechatMiniprogram.TouchEvent) {
    const type = e.currentTarget.dataset.type as string;
    this.setData({
      type,
      showDuration: type === 'meeting' || type === 'phone',
    });
  },

  onDateChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ date: e.detail.value as string });
  },

  onTimeChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ time: e.detail.value as string });
  },

  onDurationInput(e: WechatMiniprogram.Input) {
    this.setData({ duration: e.detail.value });
  },

  onSummaryInput(e: WechatMiniprogram.Input) {
    this.setData({ summary: e.detail.value });
  },

  onFollowupChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ followupDate: e.detail.value as string });
  },

  clearFollowup() {
    this.setData({ followupDate: '' });
  },

  async onSave() {
    if (!this.data.summary.trim()) {
      wx.showToast({ title: '内容必填', icon: 'none' });
      return;
    }

    this.setData({ saving: true });
    try {
      const occurred_at = `${this.data.date}T${this.data.time}:00`;
      const payload: any = {
        type: this.data.type,
        occurred_at,
        summary: this.data.summary,
      };
      if (this.data.showDuration && this.data.duration) {
        const d = parseInt(this.data.duration);
        if (!isNaN(d) && d > 0) payload.duration_min = d;
      }
      if (this.data.followupDate) {
        payload.next_followup_at = `${this.data.followupDate}T09:00:00`;
      }

      await api.post(`/api/investors/${this.data.investorId}/interactions`, payload);
      wx.showToast({ title: '已保存', icon: 'success' });
      setTimeout(() => wx.navigateBack(), 800);
    } catch (e) {/* api toast */} finally {
      this.setData({ saving: false });
    }
  },
});

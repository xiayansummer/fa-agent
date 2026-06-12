import { api } from '../../services/api';
import { formatDate } from '../../utils/time';
import { bankScheduleSubscribe } from '../../utils/subscribe';

const REMIND_OPTIONS = [
  { value: -1, label: '不提醒' },
  { value: 5, label: '提前 5 分钟' },
  { value: 15, label: '提前 15 分钟' },
  { value: 30, label: '提前 30 分钟' },
  { value: 60, label: '提前 1 小时' },
  { value: 120, label: '提前 2 小时' },
  { value: 1440, label: '提前 1 天' },
];

interface PageData {
  eventId: number;        // 0 = 新建
  title: string;
  date: string;           // YYYY-MM-DD
  startTime: string;      // HH:mm，空 = 全天
  endTime: string;
  location: string;
  notes: string;
  saving: boolean;
  isEdit: boolean;
  remindIdx: number;
  remindLabels: string[];
}

Page<PageData, {}>({
  data: {
    eventId: 0,
    title: '',
    date: '',
    startTime: '',
    endTime: '',
    location: '',
    notes: '',
    saving: false,
    isEdit: false,
    remindIdx: 3,  // 默认提前 30 分钟
    remindLabels: REMIND_OPTIONS.map(o => o.label),
  },

  onLoad(opts: { date?: string; id?: string }) {
    const id = opts.id ? parseInt(opts.id) : 0;
    if (id) {
      this.setData({ eventId: id, isEdit: true });
      this._load(id);
    } else {
      this.setData({ date: opts.date || formatDate(new Date()) });
    }
  },

  async _load(id: number) {
    try {
      const e = await api.get<any>(`/api/calendar/events/${id}`);
      const ahead = (e.remind_ahead_min === null || e.remind_ahead_min === undefined) ? 30 : e.remind_ahead_min;
      let remindIdx = REMIND_OPTIONS.findIndex(o => o.value === ahead);
      if (remindIdx < 0) remindIdx = 3;
      this.setData({
        title: e.title || '',
        date: e.date || formatDate(new Date()),
        startTime: e.start_time || '',
        endTime: e.end_time || '',
        location: e.location || '',
        notes: e.notes || '',
        remindIdx,
      });
    } catch (_e) {
      wx.showToast({ title: '加载失败', icon: 'none' });
      setTimeout(() => wx.navigateBack(), 800);
    }
  },

  onTitleInput(e: WechatMiniprogram.Input) { this.setData({ title: e.detail.value }); },
  onLocationInput(e: WechatMiniprogram.Input) { this.setData({ location: e.detail.value }); },
  onNotesInput(e: WechatMiniprogram.Input) { this.setData({ notes: e.detail.value }); },
  onDateChange(e: WechatMiniprogram.PickerChange) { this.setData({ date: e.detail.value as string }); },
  onStartChange(e: WechatMiniprogram.PickerChange) { this.setData({ startTime: e.detail.value as string }); },
  onEndChange(e: WechatMiniprogram.PickerChange) { this.setData({ endTime: e.detail.value as string }); },
  clearStart() { this.setData({ startTime: '', endTime: '' }); },
  onRemindChange(e: WechatMiniprogram.PickerChange) {
    this.setData({ remindIdx: Number(e.detail.value) });
  },
  clearEnd() { this.setData({ endTime: '' }); },

  async onSave() {
    if (!this.data.title.trim()) {
      wx.showToast({ title: '标题必填', icon: 'none' });
      return;
    }
    // 在保存手势里攒一条提醒配额。必须 await：弹窗期间停住保存流程，
    // 否则保存成功后的 navigateBack 会把授权弹窗杀掉（一闪而过点不到）。
    await bankScheduleSubscribe();
    this.setData({ saving: true });
    const payload: any = {
      title: this.data.title.trim(),
      date: this.data.date,
      start_time: this.data.startTime || null,
      end_time: this.data.endTime || null,
      location: this.data.location.trim() || null,
      notes: this.data.notes.trim() || null,
      remind_ahead_min: REMIND_OPTIONS[this.data.remindIdx].value,
    };
    try {
      if (this.data.isEdit) {
        await api.put(`/api/calendar/events/${this.data.eventId}`, payload);
      } else {
        await api.post('/api/calendar/events', payload);
      }
      wx.showToast({ title: '已保存', icon: 'success' });
      setTimeout(() => wx.navigateBack(), 700);
    } catch (_e) {/* api toast */} finally {
      this.setData({ saving: false });
    }
  },

  async onDelete() {
    const { confirm } = await wx.showModal({
      title: '删除日程',
      content: `确定删除「${this.data.title}」？`,
      confirmText: '删除',
      confirmColor: '#EF4444',
    });
    if (!confirm) return;
    try {
      await api.del(`/api/calendar/events/${this.data.eventId}`);
      wx.showToast({ title: '已删除', icon: 'success' });
      setTimeout(() => wx.navigateBack(), 700);
    } catch (_e) {/* api toast */}
  },
});

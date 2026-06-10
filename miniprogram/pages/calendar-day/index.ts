import { api } from '../../services/api';

interface CalendarEvent {
  time: string;
  type: string;
  title: string;
  description: string;
  investor_id: number;
  investor_name: string;
  action_label: string;
  action_prefill: string;
  tencent_meeting_id?: string;
  event_key?: string;
  event_id?: number;
}

interface PageData {
  date: string;
  events: CalendarEvent[];
  loading: boolean;
}

const TYPE_LABELS: Record<string, string> = {
  followup: '跟进',
  meeting: '会议',
  milestone: '里程碑',
  push: '推送',
  schedule: '日程',
};

const TYPE_COLORS: Record<string, string> = {
  followup: '#6B7AFF',
  meeting: '#10B981',
  milestone: '#F59E0B',
  push: '#8B5CF6',
  schedule: '#EC4899',
};

Page<PageData, {}>({
  data: {
    date: '',
    events: [],
    loading: false,
  },

  onLoad(opts: { date?: string }) {
    if (!opts.date) {
      wx.navigateBack();
      return;
    }
    this.setData({ date: opts.date });
    this._load();
  },

  onShow() {
    // 从 schedule-edit 返回时刷新（新建/编辑/删除后同步）
    if (this.data.date) this._load();
  },

  onAddSchedule() {
    wx.navigateTo({ url: `/pages/schedule-edit/index?date=${this.data.date}` });
  },

  async _load() {
    this.setData({ loading: true });
    try {
      const data = await api.get<{ events: CalendarEvent[] }>(`/api/calendar/daily?target_date=${this.data.date}`);
      const events = (data.events || []).map(e => ({
        ...e,
        typeLabel: TYPE_LABELS[e.type] || e.type,
        typeColor: TYPE_COLORS[e.type] || '#999',
      } as any));
      this.setData({ events });
    } finally {
      this.setData({ loading: false });
    }
  },

  async onAction(e: WechatMiniprogram.TouchEvent) {
    const event = e.currentTarget.dataset.event as CalendarEvent;

    // 自由日程 → 编辑页
    if (event.type === 'schedule') {
      wx.navigateTo({ url: `/pages/schedule-edit/index?id=${event.event_id}` });
      return;
    }

    if (event.type === 'meeting') {
      const q = event.tencent_meeting_id
        ? `meeting_id=${encodeURIComponent(event.tencent_meeting_id)}`
        : `investor_id=${event.investor_id}`;
      wx.navigateTo({ url: `/pages/meeting-prepare/index?${q}` });
      return;
    }

    try {
      const taskType = event.type === 'milestone' ? 'milestone_outreach' : 'daily_push';
      const body: any = {
        task_type: taskType,
        investor_ids: [event.investor_id],
        target_date: this.data.date,
      };
      if (event.type === 'milestone') {
        delete body.target_date;
        body.investor_id = event.investor_id;
        body.milestone_type = 'birthday';
        body.ir_name = 'IR';
        delete body.investor_ids;
      }
      const res = await api.post<{ thread_id: string }>('/api/agent/run', body);
      wx.switchTab({ url: '/pages/chat/index' });
      wx.setStorageSync('chat:incoming_thread', res.thread_id);
    } catch (e) {/* toast handled */}
  },

  async onDismiss(e: WechatMiniprogram.TouchEvent) {
    const event = e.currentTarget.dataset.event as CalendarEvent;
    if (!event.event_key) {
      wx.showToast({ title: '此事件不可删除', icon: 'none' });
      return;
    }
    // 自由日程 → 真删（DELETE），不是 dismiss
    if (event.type === 'schedule') {
      const { confirm } = await wx.showModal({
        title: '删除日程',
        content: `确定删除「${event.title}」？`,
        confirmText: '删除',
        confirmColor: '#EF4444',
      });
      if (!confirm) return;
      try {
        await api.del(`/api/calendar/events/${event.event_id}`);
        const events = this.data.events.filter(it => it.event_key !== event.event_key);
        this.setData({ events });
        wx.showToast({ title: '已删除', icon: 'success' });
      } catch (_e) {/* toast handled */}
      return;
    }

    const { confirm } = await wx.showModal({
      title: '从日历删除',
      content: `「${event.title}」这条提醒将从你的日历上移除（不影响实际会议/记录）`,
      confirmText: '删除',
      confirmColor: '#EF4444',
    });
    if (!confirm) return;
    try {
      await api.post('/api/calendar/dismiss', {
        event_key: event.event_key,
        event_date: this.data.date,
      });
      const events = this.data.events.filter(it => it.event_key !== event.event_key);
      this.setData({ events });
      wx.showToast({ title: '已删除', icon: 'success' });
    } catch (_e) {/* toast handled */}
  },
});

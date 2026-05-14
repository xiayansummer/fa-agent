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
};

const TYPE_COLORS: Record<string, string> = {
  followup: '#6B7AFF',
  meeting: '#10B981',
  milestone: '#F59E0B',
  push: '#8B5CF6',
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

import { api } from '../../services/api';
import { formatDate } from '../../utils/time';

interface Investor {
  id: number;
  name: string;
  agency?: string;
  position?: string;
  industry_tags?: string[];
  stage_pref?: string[];
  relationship_score: number;
  profile_notes?: string;
  last_interaction_at?: string;
}

interface Interaction {
  id: number;
  type: string;
  occurred_at: string;
  summary?: string;
}

interface QmingpianSummary {
  content: string;
  creator?: string;
  created_at?: string;
}

interface QmingpianHistory {
  event: string;
  agency?: string;
  industry?: string;
  round?: string;
  status?: string;
  feedback?: string;
  contact_time?: string;
}

interface QmingpianFamiliarPerson {
  name: string;
  level: string;
}

interface PageData {
  investorId: number;
  investor?: Investor;
  interactions: Interaction[];
  profileLines: string[];
  qmingpianSummaries: any[];
  qmingpianHistory: any[];
  qmingpianFamiliar: QmingpianFamiliarPerson[];
  loading: boolean;
}

const TYPE_LABELS: Record<string, string> = {
  meeting: '会议',
  email: '邮件',
  wechat: '微信',
  phone: '电话',
  call: '电话',
  push: '推送',
  other: '其他',
};

Page<PageData, {}>({
  data: {
    investorId: 0,
    investor: undefined,
    interactions: [],
    profileLines: [],
    qmingpianSummaries: [],
    qmingpianHistory: [],
    qmingpianFamiliar: [],
    loading: false,
  },

  onLoad(opts: { id?: string }) {
    if (!opts.id) {
      wx.navigateBack();
      return;
    }
    const id = parseInt(opts.id);
    this.setData({ investorId: id });
    this._load();
  },

  onShow() {
    if (this.data.investorId) this._load();
  },

  async _load() {
    this.setData({ loading: true });
    try {
      const [investor, ints] = await Promise.all([
        api.get<Investor>(`/api/investors/${this.data.investorId}`),
        api.get<Interaction[]>(`/api/investors/${this.data.investorId}/interactions?limit=5`),
      ]);

      const profileLines = (investor.profile_notes || '').split('\n').filter(Boolean);

      const interactions = ints.map(i => ({
        ...i,
        typeLabel: TYPE_LABELS[i.type] || i.type,
        dateLabel: formatDate(i.occurred_at),
      } as any));

      this.setData({ investor, interactions, profileLines });

      // 异步拉企名片纪要 + 历史推荐（不阻塞主渲染）
      if (investor?.name) {
        this._loadQmingpian(investor.name);
      }
    } finally {
      this.setData({ loading: false });
    }
  },

  async _loadQmingpian(personName: string) {
    try {
      const res = await api.get<{
        summaries?: QmingpianSummary[];
        history?: QmingpianHistory[];
        familiar_persons?: QmingpianFamiliarPerson[];
      }>(`/api/investors/qmingpian/by-name?person_name=${encodeURIComponent(personName)}`,
         { silent: true });
      const summaries = (res?.summaries || []).map((s: any) => ({
        ...s,
        // 把内容截断到前 200 字预览（详情卡片内可点击展开）
        preview: (s.content || '').slice(0, 200),
        truncated: (s.content || '').length > 200,
      }));
      this.setData({
        qmingpianSummaries: summaries,
        qmingpianHistory: res?.history || [],
        qmingpianFamiliar: res?.familiar_persons || [],
      });
    } catch {
      // silent
    }
  },

  onEdit() {
    wx.navigateTo({ url: `/pages/investor-edit/index?id=${this.data.investorId}` });
  },

  onAskAgent() {
    if (!this.data.investor) return;
    // 触发 daily_push workflow
    api.post<{ thread_id: string }>('/api/agent/run', {
      task_type: 'daily_push',
      investor_ids: [this.data.investorId],
      target_date: formatDate(new Date()),
    }).then(res => {
      wx.setStorageSync('chat:incoming_thread', res.thread_id);
      wx.switchTab({ url: '/pages/chat/index' });
    });
  },

  onAddInteraction() {
    wx.navigateTo({ url: `/pages/interaction-new/index?investor_id=${this.data.investorId}` });
  },

  previewCard(e: WechatMiniprogram.TouchEvent) {
    const url = e.currentTarget.dataset.url as string;
    if (!url) return;
    wx.previewImage({ urls: [url], current: url });
  },
});

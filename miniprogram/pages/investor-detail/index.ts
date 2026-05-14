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
  business_card_url?: string;
  avatar_url?: string;
  phone?: string[];
  wechat?: string[];
  email?: string[];
  familiarity?: string;
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
  /** 企名片侧所有名片 URL（同 person_id 聚合，第一张默认显示） */
  qmingpianCards: string[];
  /** 显示用名片 url：优先企名片 cards[0]，缺失时 fallback 本地 business_card_url */
  displayCardUrl: string;
  /** 联系方式（来自企名片 by-name 端点或本地） */
  contactPhones: string[];
  contactEmails: string[];
  contactWechats: string[];
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
    qmingpianCards: [],
    displayCardUrl: '',
    contactPhones: [],
    contactEmails: [],
    contactWechats: [],
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

      // 联系方式初始值：先用本地 investor 字段
      const localPhones = (investor as any).phone || [];
      const localEmails = (investor as any).email || [];
      const localWechats = (investor as any).wechat || [];
      this.setData({
        investor,
        interactions,
        profileLines,
        displayCardUrl: (investor as any).business_card_url || '',
        contactPhones: localPhones,
        contactEmails: localEmails,
        contactWechats: localWechats,
      });

      // 异步拉企名片纪要 + 历史推荐 + 名片 + 联系方式（不阻塞主渲染）
      if (investor?.name) {
        this._loadQmingpian(
          investor.name,
          (investor as any).agency || '',
          (investor as any).qmingpian_person_id || '',
        );
      }
    } finally {
      this.setData({ loading: false });
    }
  },

  async _loadQmingpian(personName: string, agency: string, personId: string) {
    // 并发两个端点：by-name（纪要/历史/联系方式）+ searchhit（名片列表/职务）
    let byNameQuery = `person_name=${encodeURIComponent(personName)}`;
    if (personId) byNameQuery += `&person_id=${encodeURIComponent(personId)}`;
    if (agency) byNameQuery += `&expected_agency=${encodeURIComponent(agency)}`;
    const byNamePromise = api.get<{
      agency?: string;
      phone?: string[];
      email?: string[];
      industry?: string;
      summaries?: QmingpianSummary[];
      history?: QmingpianHistory[];
      familiar_persons?: QmingpianFamiliarPerson[];
    }>(`/api/investors/qmingpian/by-name?${byNameQuery}`,
       { silent: true }).catch(() => null);

    const searchhitPromise = api.get<{
      cards?: string[];
      position?: string;
    }>(`/api/investors/qmingpian/searchhit?name=${encodeURIComponent(personName)}` +
       (agency ? `&agency=${encodeURIComponent(agency)}` : ''),
       { silent: true }).catch(() => null);

    const [byName, hit] = await Promise.all([byNamePromise, searchhitPromise]);

    const patch: any = {};

    if (byName) {
      const summaries = (byName.summaries || []).map((s: any) => ({
        ...s,
        preview: (s.content || '').slice(0, 200),
        truncated: (s.content || '').length > 200,
      }));
      patch.qmingpianSummaries = summaries;
      patch.qmingpianHistory = byName.history || [];
      patch.qmingpianFamiliar = byName.familiar_persons || [];
      // 联系方式：本地优先（名片 OCR / 手工录入更准）；企名片只补本地空字段
      const curPhones = this.data.contactPhones || [];
      const curEmails = this.data.contactEmails || [];
      if (!curPhones.length && byName.phone && byName.phone.length) {
        patch.contactPhones = byName.phone;
      }
      if (!curEmails.length && byName.email && byName.email.length) {
        patch.contactEmails = byName.email;
      }
    }

    if (hit && hit.cards && hit.cards.length) {
      patch.qmingpianCards = hit.cards;
      // 名片：默认企名片第一张（覆盖本地 fallback）
      patch.displayCardUrl = hit.cards[0];
    }

    this.setData(patch);
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
    // 如果有多张，传入全部 URL 让用户左右滑
    const all = this.data.qmingpianCards.length > 0
      ? this.data.qmingpianCards
      : [url];
    wx.previewImage({ urls: all, current: url });
  },

  onSwitchCard(e: WechatMiniprogram.TouchEvent) {
    const url = e.currentTarget.dataset.url as string;
    if (!url) return;
    this.setData({ displayCardUrl: url });
  },

  onContactTap(e: WechatMiniprogram.TouchEvent) {
    const val = e.currentTarget.dataset.val as string;
    if (!val) return;
    wx.setClipboardData({
      data: val,
      success: () => wx.showToast({ title: '已复制', icon: 'success' }),
    });
  },
});

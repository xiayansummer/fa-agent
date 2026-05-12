import { api } from '../../services/api';
import { formatRelative } from '../../utils/time';
import * as storage from '../../utils/storage';

interface LocalInvestor {
  id: number;
  qmingpian_person_id?: string;
  name: string;
  agency?: string;
  position?: string;
  avatar_url?: string;
  business_card_url?: string;
  industry_tags?: string[];
  stage_pref?: string[];
  relationship_score: number;
  last_interaction_at?: string;
}

interface SearchHit {
  qmingpian_person_id: string;
  name: string;
  agency?: string;
  local_id: number | null;
  avatar_url?: string;
  business_card_url?: string;
}

interface PageData {
  search: string;
  selectedTags: string[];
  allTags: string[];
  /** 已加入本地库的投资人（默认 Top 20）或本地匹配筛选 */
  investors: any[];
  /** 企名片搜索结果（仅当用户输入搜索关键字时显示） */
  searchHits: any[];
  /** 当前是否处于搜索模式（有 search 关键字） */
  inSearchMode: boolean;
  loading: boolean;
  adding: string;  // 正在"加入我的库"的 person_id
}

const PRESET_TAGS = ['A轮', 'B轮', 'C轮', '消费', 'TMT', '医疗', 'AI', 'SaaS', '硬件'];

Page<PageData, {}>({
  data: {
    search: '',
    selectedTags: storage.get<string[]>('investors:filter') || [],
    allTags: PRESET_TAGS,
    investors: [],
    searchHits: [],
    inSearchMode: false,
    loading: false,
    adding: '',
  },

  onLoad() {
    this._loadLocal();
  },

  onShow() {
    // 编辑后回来刷新本地列表
    if (!this.data.inSearchMode) this._loadLocal();
  },

  /** 加载"我的库"Top 20，可带 stage/industry 本地筛选 */
  async _loadLocal() {
    this.setData({ loading: true });
    try {
      // 不传 limit → 后端返回全部 is_active 投资人
      const params: string[] = [];
      if (this.data.selectedTags.length > 0) {
        const tag = this.data.selectedTags[0];
        if (['A轮', 'B轮', 'C轮'].includes(tag)) {
          params.push(`stage=${encodeURIComponent(tag)}`);
        } else {
          params.push(`industry=${encodeURIComponent(tag)}`);
        }
      }
      const url = '/api/investors' + (params.length ? '?' + params.join('&') : '');
      const data = await api.get<{ items: LocalInvestor[]; total: number }>(url);

      const investors = (data.items || []).map((inv: any) => ({
        ...inv,
        lastInteractionLabel: inv.last_interaction_at
          ? `上次沟通 ${formatRelative(inv.last_interaction_at)}`
          : '无沟通记录',
        agencyPosition: [inv.agency, inv.position].filter(Boolean).join(' · '),
        firstLetter: inv.name?.[0] || '?',
      }));
      this.setData({ investors });
    } finally {
      this.setData({ loading: false });
    }
  },

  /** 搜索企名片全库 */
  async _searchQmingpian(q: string) {
    this.setData({ loading: true });
    try {
      const data = await api.get<{ items: SearchHit[]; total: number }>(
        `/api/investors/search?q=${encodeURIComponent(q)}`
      );
      const hits = (data.items || []).map((h: any) => ({
        ...h,
        firstLetter: h.name?.[0] || '?',
        agencyOrEmpty: h.agency || '—',
        isLocal: h.local_id !== null && h.local_id !== undefined,
      }));
      this.setData({ searchHits: hits });
    } finally {
      this.setData({ loading: false });
    }
  },

  onSearchInput(e: WechatMiniprogram.Input) {
    this.setData({ search: e.detail.value });
  },

  onSearchConfirm() {
    const q = this.data.search.trim();
    if (!q) {
      // 清空搜索 → 回到本地视图
      this.setData({ inSearchMode: false, searchHits: [] });
      this._loadLocal();
      return;
    }
    this.setData({ inSearchMode: true });
    this._searchQmingpian(q);
  },

  onSearchClear() {
    this.setData({ search: '', inSearchMode: false, searchHits: [] });
    this._loadLocal();
  },

  onTagTap(e: WechatMiniprogram.TouchEvent) {
    if (this.data.inSearchMode) return;  // 搜索模式不参与标签筛选
    const tag = e.currentTarget.dataset.tag as string;
    const sel = this.data.selectedTags.includes(tag)
      ? this.data.selectedTags.filter(t => t !== tag)
      : [...this.data.selectedTags, tag];
    storage.set('investors:filter', sel);
    this.setData({ selectedTags: sel }, () => this._loadLocal());
  },

  /** 点击本地投资人卡片 → 跳详情 */
  onInvestorTap(e: WechatMiniprogram.TouchEvent) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: `/pages/investor-detail/index?id=${id}` });
  },

  /** 点击搜索结果 */
  onHitTap(e: WechatMiniprogram.TouchEvent) {
    const hit = e.currentTarget.dataset.hit as SearchHit;
    if (hit.local_id) {
      // 已在本地 → 跳详情
      wx.navigateTo({ url: `/pages/investor-detail/index?id=${hit.local_id}` });
    } else {
      // 未加入 → 跳编辑页（带 person_id 预填）
      wx.navigateTo({
        url: `/pages/investor-edit/index?qmingpian_person_id=${encodeURIComponent(hit.qmingpian_person_id)}&name=${encodeURIComponent(hit.name)}&agency=${encodeURIComponent(hit.agency || '')}`,
      });
    }
  },

  /** 快速加入：直接 POST，不进编辑页 */
  async onQuickAdd(e: WechatMiniprogram.TouchEvent) {
    const hit = e.currentTarget.dataset.hit as SearchHit;
    if (this.data.adding) return;
    this.setData({ adding: hit.qmingpian_person_id });
    try {
      await api.post<any>('/api/investors', {
        qmingpian_person_id: hit.qmingpian_person_id,
        name: hit.name,
        agency: hit.agency || '',
      });
      wx.showToast({ title: '已加入', icon: 'success' });
      // 刷新搜索结果，让此条变成 isLocal
      this._searchQmingpian(this.data.search.trim());
    } catch (err) {
      // api toast handles
    } finally {
      this.setData({ adding: '' });
    }
  },

  onAdd() {
    wx.navigateTo({ url: '/pages/investor-edit/index' });
  },
});

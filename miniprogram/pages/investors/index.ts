import { api } from '../../services/api';
import { formatRelative } from '../../utils/time';
import * as storage from '../../utils/storage';

interface Investor {
  id: number;
  name: string;
  agency?: string;
  position?: string;
  industry_tags?: string[];
  stage_pref?: string[];
  relationship_score: number;
  last_interaction_at?: string;
}

interface PageData {
  search: string;
  selectedTags: string[];
  allTags: string[];
  investors: Investor[];
  loading: boolean;
}

const PRESET_TAGS = ['A轮', 'B轮', 'C轮', '消费', 'TMT', '医疗', 'AI', 'SaaS', '硬件'];

Page<PageData, {}>({
  data: {
    search: '',
    selectedTags: storage.get<string[]>('investors:filter') || [],
    allTags: PRESET_TAGS,
    investors: [],
    loading: false,
  },

  onLoad() {
    this._load();
  },

  onShow() {
    // 编辑后回来刷新
    this._load();
  },

  async _load() {
    this.setData({ loading: true });
    try {
      const params: string[] = [];
      if (this.data.search) params.push(`q=${encodeURIComponent(this.data.search)}`);
      // 简化：用 selectedTags 第一个做 industry filter（后端 single value）
      if (this.data.selectedTags.length > 0) {
        const tag = this.data.selectedTags[0];
        if (['A轮', 'B轮', 'C轮'].includes(tag)) {
          params.push(`stage=${encodeURIComponent(tag)}`);
        } else {
          params.push(`industry=${encodeURIComponent(tag)}`);
        }
      }
      const data = await api.get<{ items: Investor[]; total: number }>(
        `/api/investors${params.length ? '?' + params.join('&') : ''}`
      );

      // 后处理：添加 lastInteractionLabel
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

  onSearchInput(e: WechatMiniprogram.Input) {
    this.setData({ search: e.detail.value });
  },

  onSearchConfirm() {
    this._load();
  },

  onTagTap(e: WechatMiniprogram.TouchEvent) {
    const tag = e.currentTarget.dataset.tag as string;
    const sel = this.data.selectedTags.includes(tag)
      ? this.data.selectedTags.filter(t => t !== tag)
      : [...this.data.selectedTags, tag];
    storage.set('investors:filter', sel);
    this.setData({ selectedTags: sel }, () => this._load());
  },

  onInvestorTap(e: WechatMiniprogram.TouchEvent) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: `/pages/investor-detail/index?id=${id}` });
  },

  onAdd() {
    wx.navigateTo({ url: '/pages/investor-edit/index' });
  },
});

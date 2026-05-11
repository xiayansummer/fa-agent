import { api } from '../../services/api';
import { formatRelative } from '../../utils/time';

interface OutreachRecord {
  id: number;
  investor_id: number;
  type: string;
  channel: string;
  content?: string;
  status: string;
  created_at: string;
}

interface PageData {
  records: any[];   // with computed fields
  filterType: string;  // '' | 'meeting_minutes' | 'industry_report' | ...
  filterTypes: { value: string; label: string }[];
  loading: boolean;
}

const TYPE_LABELS: Record<string, string> = {
  meeting_minutes: '会议纪要',
  industry_report: '行业推送',
  daily_push: '每日推送',
  milestone_message: '节点关怀',
};

const STATUS_LABELS: Record<string, string> = {
  draft: '待审核',
  approved: '已通过',
  sent: '已发送',
  failed: '已失败',
};

const STATUS_COLORS: Record<string, string> = {
  draft: '#F59E0B',
  approved: '#10B981',
  sent: '#6B7AFF',
  failed: '#DC2626',
};

Page<PageData, {}>({
  data: {
    records: [],
    filterType: '',
    filterTypes: [
      { value: '', label: '全部' },
      { value: 'meeting_minutes', label: '会议纪要' },
      { value: 'industry_report', label: '行业推送' },
      { value: 'daily_push', label: '每日推送' },
      { value: 'milestone_message', label: '节点关怀' },
    ],
    loading: false,
  },

  onLoad() {
    this._load();
  },

  async _load() {
    this.setData({ loading: true });
    try {
      const params = this.data.filterType ? `?type=${this.data.filterType}&limit=100` : '?limit=100';
      const records = await api.get<OutreachRecord[]>(`/api/outreach/history${params}`);

      const enriched = records.map(r => ({
        ...r,
        typeLabel: TYPE_LABELS[r.type] || r.type,
        statusLabel: STATUS_LABELS[r.status] || r.status,
        statusColor: STATUS_COLORS[r.status] || '#999',
        createdLabel: formatRelative(r.created_at),
        contentPreview: (r.content || '').slice(0, 80),
      }));
      this.setData({ records: enriched });
    } finally {
      this.setData({ loading: false });
    }
  },

  onFilterTap(e: WechatMiniprogram.TouchEvent) {
    const value = e.currentTarget.dataset.value as string;
    this.setData({ filterType: value }, () => this._load());
  },

  onItemTap(e: WechatMiniprogram.TouchEvent) {
    const item = e.currentTarget.dataset.item as any;
    // 简化：弹 modal 显示完整内容
    wx.showModal({
      title: `${item.typeLabel} · ${item.statusLabel}`,
      content: item.content || '（无内容）',
      showCancel: false,
      confirmText: '关闭',
    });
  },
});

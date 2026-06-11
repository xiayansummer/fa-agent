import { api } from '../../services/api';
import { formatDate } from '../../utils/time';

interface MonthData {
  month: string;
  days: Record<string, string[]>;
}

const TYPE_COLORS: Record<string, string> = {
  followup: '#6B7AFF',
  milestone: '#F59E0B',
  meeting: '#10B981',
  push: '#8B5CF6',
  schedule: '#EC4899',
};

const TYPE_LABELS: Record<string, string> = {
  followup: '跟进',
  meeting: '会议',
  milestone: '里程碑',
  push: '推送',
  schedule: '日程',
};

interface DayCell {
  date: string;       // YYYY-MM-DD
  day: number;        // 1-31
  inMonth: boolean;
  isToday: boolean;
  types: string[];
  colors: string[];
}

interface CalendarData {
  currentYear: number;
  currentMonth: number;     // 1-12
  monthNum2: string;        // "06"
  dayCells: DayCell[];
  selectedDate: string;     // 选中日期，默认今天
  selLabel: string;         // "今日" / "06-15"
  selEvents: any[];
  loading: boolean;
}

Page<CalendarData, {}>({
  data: {
    currentYear: new Date().getFullYear(),
    currentMonth: new Date().getMonth() + 1,
    monthNum2: '',
    dayCells: [],
    selectedDate: '',
    selLabel: '今日',
    selEvents: [],
    loading: false,
  },

  onLoad() {
    const today = formatDate(new Date());
    this.setData({ selectedDate: today });
    this._renderMonth();
    this._loadMonth();
    this._loadDay(today);
  },

  onShow() {
    // 从其他页（如 calendar-day dismiss / schedule-edit）回来时同步刷新
    this._loadMonth();
    if (this.data.selectedDate) this._loadDay(this.data.selectedDate);
  },

  _renderMonth() {
    const { currentYear, currentMonth } = this.data;
    const monthNum2 = String(currentMonth).padStart(2, '0');
    const firstDay = new Date(currentYear, currentMonth - 1, 1);
    // 周一开头：周一 offset 0 … 周日 offset 6
    const offset = (firstDay.getDay() + 6) % 7;

    const today = formatDate(new Date());
    const cells: DayCell[] = [];
    for (let i = 0; i < 42; i++) {
      const d = new Date(currentYear, currentMonth - 1, 1 - offset + i);
      const dateStr = formatDate(d);
      cells.push({
        date: dateStr,
        day: d.getDate(),
        inMonth: d.getMonth() === currentMonth - 1,
        isToday: dateStr === today,
        types: [],
        colors: [],
      });
    }
    this.setData({ monthNum2, dayCells: cells });
  },

  async _loadMonth() {
    this.setData({ loading: true });
    try {
      const month = `${this.data.currentYear}-${String(this.data.currentMonth).padStart(2, '0')}`;
      const data = await api.get<MonthData>(`/api/calendar/month?month=${month}`);

      const updates: Partial<CalendarData> = { dayCells: this.data.dayCells.slice() };
      updates.dayCells!.forEach((cell) => {
        const types = data.days[cell.date] || [];
        cell.types = types;
        cell.colors = types.map((t) => TYPE_COLORS[t] || '#999').slice(0, 4); // 最多 4 个点
      });
      this.setData(updates);
    } catch (e) {
      // toast 由 api.ts 处理
    } finally {
      this.setData({ loading: false });
    }
  },

  async _loadDay(date: string) {
    const today = formatDate(new Date());
    this.setData({ selLabel: date === today ? '今日' : date.slice(5).replace('-', '月') + '日' });
    try {
      const data = await api.get<{ events: any[] }>(`/api/calendar/daily?target_date=${date}`);
      const events = (data.events || []).map((e) => ({
        ...e,
        typeLabel: TYPE_LABELS[e.type] || e.type,
        typeColor: TYPE_COLORS[e.type] || '#999',
      }));
      this.setData({ selEvents: events });
    } catch (e) {/* silent */}
  },

  prevMonth() {
    let { currentYear, currentMonth } = this.data;
    if (currentMonth === 1) { currentMonth = 12; currentYear -= 1; }
    else { currentMonth -= 1; }
    this.setData({ currentYear, currentMonth }, () => {
      this._renderMonth();
      this._loadMonth();
    });
  },

  nextMonth() {
    let { currentYear, currentMonth } = this.data;
    if (currentMonth === 12) { currentMonth = 1; currentYear += 1; }
    else { currentMonth += 1; }
    this.setData({ currentYear, currentMonth }, () => {
      this._renderMonth();
      this._loadMonth();
    });
  },

  onDayTap(e: WechatMiniprogram.TouchEvent) {
    const date = e.currentTarget.dataset.date as string;
    const cell = this.data.dayCells.find((c) => c.date === date);
    if (!cell || !cell.inMonth) return;
    this.setData({ selectedDate: date });
    this._loadDay(date);
  },

  /** 进入该日完整管理页（执行/删除/新建日程） */
  goDayDetail() {
    if (!this.data.selectedDate) return;
    wx.navigateTo({ url: `/pages/calendar-day/index?date=${this.data.selectedDate}` });
  },

  onEventTap() {
    this.goDayDetail();
  },

  onAddSchedule() {
    wx.navigateTo({ url: `/pages/schedule-edit/index?date=${this.data.selectedDate || formatDate(new Date())}` });
  },

  goMe() {
    wx.navigateTo({ url: '/pages/me/index' });
  },
});

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
  monthLabel: string;       // "2026 年 5 月"
  dayCells: DayCell[];
  todayEvents: any[];
  loading: boolean;
}

Page<CalendarData, {}>({
  data: {
    currentYear: new Date().getFullYear(),
    currentMonth: new Date().getMonth() + 1,
    monthLabel: '',
    dayCells: [],
    todayEvents: [],
    loading: false,
  },

  onLoad() {
    this._renderMonth();
    this._loadMonth();
    this._loadToday();
  },

  onShow() {
    // tab 切换回来时刷新今日数据（可能其他 tab 改了）
    this._loadToday();
  },

  _renderMonth() {
    const { currentYear, currentMonth } = this.data;
    const monthLabel = `${currentYear} 年 ${currentMonth} 月`;
    const firstDay = new Date(currentYear, currentMonth - 1, 1);
    const lastDay = new Date(currentYear, currentMonth, 0).getDate();
    const startWeekday = firstDay.getDay(); // 0=Sun

    const today = formatDate(new Date());
    const cells: DayCell[] = [];

    // 上个月填充
    const prevLast = new Date(currentYear, currentMonth - 1, 0).getDate();
    for (let i = startWeekday - 1; i >= 0; i--) {
      const day = prevLast - i;
      const dateStr = `${currentYear}-${String(currentMonth - 1 || 12).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
      cells.push({ date: dateStr, day, inMonth: false, isToday: false, types: [], colors: [] });
    }

    // 当前月
    for (let day = 1; day <= lastDay; day++) {
      const dateStr = `${currentYear}-${String(currentMonth).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
      cells.push({
        date: dateStr,
        day,
        inMonth: true,
        isToday: dateStr === today,
        types: [],
        colors: [],
      });
    }

    // 下个月填充至 6 行（42 格）
    while (cells.length < 42) {
      const day = cells.length - lastDay - startWeekday + 1;
      const dateStr = `${currentYear}-${String(currentMonth + 1 > 12 ? 1 : currentMonth + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
      cells.push({ date: dateStr, day, inMonth: false, isToday: false, types: [], colors: [] });
    }

    this.setData({ monthLabel, dayCells: cells });
  },

  async _loadMonth() {
    this.setData({ loading: true });
    try {
      const month = `${this.data.currentYear}-${String(this.data.currentMonth).padStart(2, '0')}`;
      const data = await api.get<MonthData>(`/api/calendar/month?month=${month}`);

      // 把 types 写入对应 dayCells
      const updates: Partial<CalendarData> = { dayCells: this.data.dayCells.slice() };
      updates.dayCells!.forEach((cell) => {
        const types = data.days[cell.date] || [];
        cell.types = types;
        cell.colors = types.map((t) => TYPE_COLORS[t] || '#999').slice(0, 3); // 最多 3 个点
      });
      this.setData(updates);
    } catch (e) {
      // toast 由 api.ts 处理
    } finally {
      this.setData({ loading: false });
    }
  },

  async _loadToday() {
    try {
      const today = formatDate(new Date());
      const data = await api.get<{ events: any[] }>(`/api/calendar/daily?target_date=${today}`);
      this.setData({ todayEvents: (data.events || []).slice(0, 3) });
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
    wx.navigateTo({ url: `/pages/calendar-day/index?date=${date}` });
  },

  goMe() {
    // F9 实现
    wx.showToast({ title: '我的页面（F9 实现）', icon: 'none' });
  },
});

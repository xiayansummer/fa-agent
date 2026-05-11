import { api } from '../../services/api';
import { wsManager, type WSEvent } from '../../services/ws';
import { formatDate } from '../../utils/time';

interface Message {
  id: string;
  kind: 'user' | 'agent_card' | 'thinking' | 'agent_text';
  agent?: string;
  title?: string;
  body?: string;
  actions?: any[];
  showStatus?: string;
  text?: string;
  threadId?: string;  // 关联的 thread_id（用于 review）
  thinkingLabel?: string;
}

interface CalendarEvent {
  time: string;
  type: string;
  title: string;
  investor_id: number;
  investor_name: string;
  action_label: string;
  action_prefill: string;
}

interface PageData {
  messages: Message[];
  input: string;
  scrollToView: string;
  history: { role: 'user' | 'assistant'; content: string }[];
  currentThreadId: string;
}

Page<PageData, {}>({
  data: {
    messages: [],
    input: '',
    scrollToView: '',
    history: [],
    currentThreadId: '',
  },

  onLoad() {
    this._loadOrchestratorGreeting();
  },

  onShow() {
    // 检查是否有从其他页跳进来的 thread_id
    const incoming = wx.getStorageSync('chat:incoming_thread');
    if (incoming && incoming !== this.data.currentThreadId) {
      wx.removeStorageSync('chat:incoming_thread');
      this._subscribeToThread(incoming);
    }
  },

  onUnload() {
    if (this.data.currentThreadId) {
      wsManager.unsubscribe(this.data.currentThreadId);
    }
  },

  async _loadOrchestratorGreeting() {
    try {
      const today = formatDate(new Date());
      const data = await api.get<{ events: CalendarEvent[] }>(
        `/api/calendar/daily?target_date=${today}`,
        { silent: true }
      );
      const events = data.events || [];
      const counts = { followup: 0, meeting: 0, milestone: 0, push: 0 };
      events.forEach((e) => {
        if (e.type in counts) counts[e.type as keyof typeof counts] += 1;
      });

      const summaryParts: string[] = [];
      if (counts.followup) summaryParts.push(`${counts.followup} 个跟进`);
      if (counts.meeting) summaryParts.push(`${counts.meeting} 场会议`);
      if (counts.milestone) summaryParts.push(`${counts.milestone} 个里程碑`);
      if (counts.push) summaryParts.push(`${counts.push} 条推送`);

      const body = summaryParts.length > 0
        ? `早上好，今日有 ${summaryParts.join('、')}`
        : '早上好，今天没有 Agent 安排的任务，可以问我任何问题';

      this._appendMessage({
        id: `o-${Date.now()}`,
        kind: 'agent_card',
        agent: 'orchestrator',
        title: 'Orchestrator · 统筹',
        body,
        actions: events.length > 0 ? [{ action: 'view_calendar', label: '查看日程', primary: true }] : [],
      });
    } catch (e) {
      // silent
    }
  },

  _appendMessage(msg: Message) {
    this.setData({
      messages: [...this.data.messages, msg],
      scrollToView: msg.id,
    });
  },

  _replaceMessage(id: string, patch: Partial<Message>) {
    const messages = this.data.messages.map((m) =>
      m.id === id ? { ...m, ...patch } : m
    );
    this.setData({ messages, scrollToView: id });
  },

  onInput(e: WechatMiniprogram.Input) {
    this.setData({ input: e.detail.value });
  },

  async onSend() {
    const text = this.data.input.trim();
    if (!text) return;
    this.setData({ input: '' });

    const userMsgId = `u-${Date.now()}`;
    this._appendMessage({ id: userMsgId, kind: 'user', text });

    // thinking
    const thinkingId = `t-${Date.now()}`;
    this._appendMessage({
      id: thinkingId,
      kind: 'thinking',
      agent: 'content',
      thinkingLabel: '正在回复',
    });

    try {
      const res = await api.post<{ reply: string }>('/api/agent/chat', {
        message: text,
        history: this.data.history.slice(-10),
      });

      // 替换 thinking 为 agent_text
      this._replaceMessage(thinkingId, {
        kind: 'agent_text',
        agent: 'content',
        body: res.reply,
      });

      // 更新 history
      const newHistory = [
        ...this.data.history,
        { role: 'user' as const, content: text },
        { role: 'assistant' as const, content: res.reply },
      ];
      this.setData({ history: newHistory.slice(-10) });
    } catch (e) {
      this._replaceMessage(thinkingId, {
        kind: 'agent_text',
        agent: 'content',
        body: '回复失败，请重试',
      });
    }
  },

  _subscribeToThread(threadId: string) {
    if (this.data.currentThreadId) {
      wsManager.unsubscribe(this.data.currentThreadId);
    }
    this.setData({ currentThreadId: threadId });

    // 注入 thinking 卡占位
    const thinkingId = `wt-${threadId}`;
    this._appendMessage({
      id: thinkingId,
      kind: 'thinking',
      agent: 'content',
      thinkingLabel: '正在处理',
      threadId,
    });

    wsManager.subscribe(threadId, (event: WSEvent) => {
      this._handleWSEvent(threadId, thinkingId, event);
    });
  },

  _handleWSEvent(threadId: string, thinkingId: string, event: WSEvent) {
    if (event.type === 'node_done') {
      // 更新 thinking 文案
      this._replaceMessage(thinkingId, {
        thinkingLabel: `${event.node} 完成`,
      });
    } else if (event.type === 'waiting_review') {
      // thinking 卡 → agent-card with review actions
      this._replaceMessage(thinkingId, {
        kind: 'agent_card',
        agent: 'content',
        title: '内容 Agent · 待审核',
        body: event.draft,
        actions: [
          { action: 'approve', label: '通过', primary: true },
          { action: 'modify', label: '调整' },
          { action: 'reject', label: '拒绝' },
        ],
        threadId,
      });
    } else if (event.type === 'done') {
      this._replaceMessage(thinkingId, {
        kind: 'agent_card',
        agent: 'content',
        title: '内容 Agent',
        body: event.final || '',
        actions: [],
        showStatus: '已通过',
      });
      this.setData({ currentThreadId: '' });
    } else if (event.type === 'error') {
      this._replaceMessage(thinkingId, {
        kind: 'agent_card',
        agent: 'content',
        title: '错误',
        body: event.message,
        actions: [{ action: 'retry', label: '重试' }],
      });
      this.setData({ currentThreadId: '' });
    } else if (event.type === 'snapshot') {
      // 重连 fallback
      if (event.status === 'waiting_review' && event.draft) {
        this._replaceMessage(thinkingId, {
          kind: 'agent_card',
          agent: 'content',
          title: '内容 Agent · 待审核',
          body: event.draft,
          actions: [
            { action: 'approve', label: '通过', primary: true },
            { action: 'modify', label: '调整' },
            { action: 'reject', label: '拒绝' },
          ],
          threadId,
        });
      } else if (event.status === 'done') {
        this._replaceMessage(thinkingId, {
          kind: 'agent_card',
          agent: 'content',
          title: '内容 Agent',
          body: event.final || '',
          actions: [],
          showStatus: '已通过',
        });
      } else if (event.status === 'error') {
        this._replaceMessage(thinkingId, {
          kind: 'agent_card',
          agent: 'content',
          title: '错误',
          body: event.error || '未知错误',
          actions: [{ action: 'retry', label: '重试' }],
        });
      } else {
        this._replaceMessage(thinkingId, {
          thinkingLabel: `状态: ${event.status}`,
        });
      }
    }
  },

  onCardAction(e: WechatMiniprogram.CustomEvent<{ action: string }>) {
    // F4c 实现真实 review POST
    wx.showToast({ title: `action: ${e.detail.action}（F4c 实现）`, icon: 'none' });
  },
});

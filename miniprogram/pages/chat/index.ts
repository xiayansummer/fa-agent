/**
 * F4a: 静态骨架，展示 mock 数据。
 * F4b 接入 WS + 早安卡。
 * F4c 接入审核交互。
 */

interface Message {
  id: string;
  kind: 'user' | 'agent_card' | 'thinking';
  agent?: string;       // for agent_card
  title?: string;
  body?: string;
  actions?: any[];
  showStatus?: string;
  text?: string;        // for user
}

interface PageData {
  messages: Message[];
  input: string;
}

Page<PageData, {}>({
  data: {
    messages: [
      // mock 数据
      {
        id: 'm1', kind: 'agent_card', agent: 'orchestrator',
        title: 'Orchestrator · 统筹',
        body: '早上好，今日有 3 个待处理任务',
        actions: [{ action: 'view_tasks', label: '查看', primary: true }],
      },
      {
        id: 'm2', kind: 'agent_card', agent: 'list',
        title: '名单 Agent',
        body: '今日跟进名单 5 人：张伟、李明、王芳、刘强、赵丽',
        actions: [
          { action: 'approve', label: '✓ 确认', primary: true },
          { action: 'modify', label: '调整' },
        ],
      },
      { id: 'm3', kind: 'user', text: '帮我写张伟的会议纪要' },
      { id: 'm4', kind: 'thinking', agent: 'content', },
    ],
    input: '',
  },

  onLoad() {/* F4b 接入 */},

  onInput(e: WechatMiniprogram.Input) {
    this.setData({ input: e.detail.value });
  },

  onSend() {
    const text = this.data.input.trim();
    if (!text) return;
    this.setData({
      input: '',
      messages: [
        ...this.data.messages,
        { id: `u-${Date.now()}`, kind: 'user', text },
      ],
    });
    // F4b: POST /api/agent/chat
  },

  onCardAction(e: WechatMiniprogram.CustomEvent<{ action: string }>) {
    // F4c: POST /api/agent/{thread_id}/review
    wx.showToast({ title: `action: ${e.detail.action}（F4c 实现）`, icon: 'none' });
  },
});

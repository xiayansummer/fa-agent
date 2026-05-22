import { api } from '../../services/api';
import { wsManager, type WSEvent } from '../../services/ws';
import { formatDate } from '../../utils/time';
import { mdToHtml } from '../../utils/markdown';

interface OrchAction {
  label: string;
  task_type: 'daily_push' | 'meeting_minutes' | 'milestone_outreach' | 'smart_list' | 'navigate';
  investor_id?: number;
  tencent_meeting_id?: string;
  target?: 'drafts' | 'calendar' | 'investors';
  milestone_type?: string;
}

interface Message {
  id: string;
  kind: 'user' | 'agent_card' | 'thinking' | 'agent_text';
  agent?: string;
  title?: string;
  body?: string;
  /** body 渲染版（HTML for <rich-text>），用于 agent_text 类 markdown 输出 */
  bodyHtml?: string;
  actions?: any[];
  showStatus?: string;
  text?: string;
  /** user kind：附加的图片预览（本地 tempFilePath 或上传后的签名 URL） */
  imageUrl?: string;
  threadId?: string;  // 关联的 thread_id（用于 review）
  thinkingLabel?: string;
  inlineEditable?: boolean;  // 短内容才允许内联编辑
  /** Orchestrator 简报卡片的原始 action 列表（actions 里 data-action=orch_N 时按 index 查这个） */
  orchActions?: OrchAction[];
  /** 该 thread 的 task_type（来自 waiting_review 事件），决定审核时是否要让 IR 选投资人 */
  taskType?: string;
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

interface PendingFile {
  url: string;
  name: string;
  purpose: 'doc' | 'image';
}

interface PageData {
  messages: Message[];
  input: string;
  scrollToView: string;
  history: { role: 'user' | 'assistant'; content: string }[];
  currentThreadId: string;
  modalVisible: boolean;
  modalBody: string;
  modalAgentTitle: string;
  modalThreadId: string;
  modalTaskType: string;
  pendingFile: PendingFile | null;
}

Page<PageData, {}>({
  data: {
    messages: [],
    input: '',
    scrollToView: '',
    history: [],
    currentThreadId: '',
    modalVisible: false,
    modalBody: '',
    modalAgentTitle: '',
    modalThreadId: '',
    modalTaskType: '',
    pendingFile: null,
  },

  onLoad() {
    this._runOrchestratorBriefing();
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

  /** 启动 Orchestrator briefing workflow → WS 推回结构化 brief → 渲染早安卡。 */
  _runOrchestratorBriefing() {
    const placeholderId = `o-${Date.now()}`;
    this._appendMessage({
      id: placeholderId,
      kind: 'thinking',
      agent: 'orchestrator',
      thinkingLabel: '正在分析今日信号',
    });

    api.post<{ thread_id: string }>('/api/agent/run', {
      task_type: 'briefing',
      target_date: formatDate(new Date()),
    }, { silent: true }).then((res) => {
      const threadId = res.thread_id;
      wsManager.subscribe(threadId, (event: WSEvent) => {
        if (event.type === 'done') {
          let parsed: any = null;
          try { parsed = JSON.parse(event.final || '{}'); } catch {}
          if (!parsed || !parsed.greeting) {
            this._replaceMessage(placeholderId, {
              kind: 'agent_text',
              agent: 'orchestrator',
              body: '早上好，可以问我任何问题',
            });
            return;
          }
          const lines: string[] = [parsed.greeting];
          for (const h of (parsed.highlights || []).slice(0, 2)) {
            lines.push(`• ${h}`);
          }
          const orchActions: OrchAction[] = (parsed.suggested_actions || []).slice(0, 3);
          this._replaceMessage(placeholderId, {
            kind: 'agent_card',
            agent: 'orchestrator',
            title: 'Orchestrator · 统筹',
            body: lines.join('\n\n'),
            actions: orchActions.map((a, i) => ({
              action: `orch_${i}`,
              label: a.label,
              primary: i === 0,
            })),
            orchActions,
          });
        } else if (event.type === 'error' || event.type === 'snapshot' && event.status === 'error') {
          this._replaceMessage(placeholderId, {
            kind: 'agent_text',
            agent: 'orchestrator',
            body: '早上好，可以问我任何问题',
          });
        } else if (event.type === 'snapshot' && event.status === 'done' && event.final) {
          // 重连 fallback：直接走 done 分支逻辑
          let parsed: any = null;
          try { parsed = JSON.parse(event.final); } catch {}
          if (parsed?.greeting) {
            const orchActions: OrchAction[] = (parsed.suggested_actions || []).slice(0, 3);
            this._replaceMessage(placeholderId, {
              kind: 'agent_card',
              agent: 'orchestrator',
              title: 'Orchestrator · 统筹',
              body: [parsed.greeting, ...(parsed.highlights || []).slice(0, 2).map((h: string) => `• ${h}`)].join('\n\n'),
              actions: orchActions.map((a, i) => ({ action: `orch_${i}`, label: a.label, primary: i === 0 })),
              orchActions,
            });
          }
        }
      });
    }).catch(() => {
      this._replaceMessage(placeholderId, {
        kind: 'agent_text',
        agent: 'orchestrator',
        body: '早上好，可以问我任何问题',
      });
    });
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
    const pending = this.data.pendingFile;
    if (!text && !pending) return;
    this.setData({ input: '', pendingFile: null });

    // pendingFile 存在时：图片走缩略图，文档走 chip 文本
    const displayText = pending
      ? (pending.purpose === 'image'
          ? text                                     // 图片：bubble 只显文字（图另起一块）
          : `📎 ${pending.name}${text ? '\n' + text : ''}`)
      : text;
    const sendText = pending
      ? `[IR 已上传${pending.purpose === 'image' ? '图片' : '文档'} url=${pending.url} 文件名=${pending.name}] ${text || '请根据上下文处理这个文件，或反问 IR 想做什么'}`
      : text;

    const userMsgId = `u-${Date.now()}`;
    this._appendMessage({
      id: userMsgId,
      kind: 'user',
      text: displayText,
      imageUrl: pending && pending.purpose === 'image' ? pending.url : undefined,
    });

    // thinking
    const thinkingId = `t-${Date.now()}`;
    this._appendMessage({
      id: thinkingId,
      kind: 'thinking',
      agent: 'orchestrator',
      thinkingLabel: '正在思考',
    });

    try {
      const res = await api.post<{ reply: string; agent_role?: string; thread_id?: string }>('/api/agent/chat', {
        message: sendText,
        history: this.data.history.slice(-10),
      });

      // 替换 thinking 为 agent_text；agent_role 默认 orchestrator
      this._replaceMessage(thinkingId, {
        kind: 'agent_text',
        agent: res.agent_role || 'orchestrator',
        body: res.reply,
        bodyHtml: mdToHtml(res.reply || ''),
      });

      // 如果 Orchestrator 触发了 workflow，自动接管 WS 显示其它 agent 的进度
      if (res.thread_id) {
        this._subscribeToThread(res.thread_id);
      }

      // 更新 history
      const newHistory = [
        ...this.data.history,
        { role: 'user' as const, content: sendText },
        { role: 'assistant' as const, content: res.reply },
      ];
      this.setData({ history: newHistory.slice(-10) });
    } catch (e) {
      this._replaceMessage(thinkingId, {
        kind: 'agent_text',
        agent: 'orchestrator',
        body: '回复失败，请重试',
        bodyHtml: '<p>回复失败，请重试</p>',
      });
    }
  },

  /** 处理 Orchestrator 建议按钮：根据 task_type 跳页 / 启 workflow。 */
  async _dispatchOrchAction(orch: OrchAction) {
    if (orch.task_type === 'navigate') {
      if (orch.target === 'drafts') {
        wx.navigateTo({ url: '/pages/drafts/index' });
      } else if (orch.target === 'calendar') {
        wx.switchTab({ url: '/pages/calendar/index' });
      } else if (orch.target === 'investors') {
        wx.switchTab({ url: '/pages/investors/index' });
      }
      return;
    }
    if (orch.task_type === 'meeting_minutes' && orch.tencent_meeting_id) {
      wx.navigateTo({
        url: `/pages/meeting-prepare/index?meeting_id=${encodeURIComponent(orch.tencent_meeting_id)}`,
      });
      return;
    }
    // daily_push / milestone_outreach / smart_list → 启 workflow 并订阅
    const body: any = { task_type: orch.task_type };
    if (orch.investor_id) {
      if (orch.task_type === 'milestone_outreach') {
        body.investor_id = orch.investor_id;
        body.milestone_type = orch.milestone_type || 'birthday';
      } else {
        body.investor_ids = [orch.investor_id];
      }
    }
    body.target_date = formatDate(new Date());
    try {
      const res = await api.post<{ thread_id: string }>('/api/agent/run', body);
      this._subscribeToThread(res.thread_id);
    } catch (err) {
      wx.showToast({ title: '启动失败', icon: 'none' });
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
      const isShort = (event.draft || '').length < 200;
      this._replaceMessage(thinkingId, {
        kind: 'agent_card',
        agent: 'content',
        title: '内容 Agent · 待审核',
        body: event.draft,
        actions: [
          { action: 'approve', label: '通过', primary: true },
          { action: 'modify', label: isShort ? '调整' : '展开编辑' },
          { action: 'reject', label: '拒绝' },
        ],
        threadId,
        inlineEditable: isShort,
        taskType: event.task_type,
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
        const isShort = (event.draft || '').length < 200;
        this._replaceMessage(thinkingId, {
          kind: 'agent_card',
          agent: 'content',
          title: '内容 Agent · 待审核',
          body: event.draft,
          taskType: event.task_type,
          actions: [
            { action: 'approve', label: '通过', primary: true },
            { action: 'modify', label: isShort ? '调整' : '展开编辑' },
            { action: 'reject', label: '拒绝' },
          ],
          threadId,
          inlineEditable: isShort,
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

  async onCardAction(e: WechatMiniprogram.CustomEvent<{ action: string; final?: string }>) {
    const { action, final } = e.detail;

    if (action === 'view_calendar') {
      wx.switchTab({ url: '/pages/calendar/index' });
      return;
    }

    // Orchestrator suggested_actions：action 编码为 orch_<index>
    if (action.startsWith('orch_')) {
      const idx = parseInt(action.slice(5));
      const msg = [...this.data.messages].reverse().find(
        (m) => m.agent === 'orchestrator' && m.orchActions && m.orchActions.length > idx
      );
      const orch = msg?.orchActions?.[idx];
      if (!orch) return;
      await this._dispatchOrchAction(orch);
      return;
    }

    if (action === 'retry') {
      wx.showToast({ title: '请重新发起任务', icon: 'none' });
      return;
    }

    // approve / modify / reject
    if (!['approve', 'modify', 'reject'].includes(action)) {
      wx.showToast({ title: `未知动作: ${action}`, icon: 'none' });
      return;
    }

    // 找到对应消息（遍历找最近一个有 threadId 且非 done 的 agent_card）
    const msg = [...this.data.messages].reverse().find(
      (m) => m.threadId && m.kind === 'agent_card' && !m.showStatus
    );
    if (!msg || !msg.threadId) {
      wx.showToast({ title: '找不到关联会话', icon: 'none' });
      return;
    }

    // 长内容点 modify（!inlineEditable），且没有 final（即第一次点，不是提交）
    if (action === 'modify' && !final && msg.body && msg.body.length >= 200) {
      // 长内容 modify → 弹 Modal
      this.setData({
        modalVisible: true,
        modalAgentTitle: msg.title || '内容 Agent',
        modalBody: msg.body,
        modalThreadId: msg.threadId,
        modalTaskType: msg.taskType || '',
      });
      return;
    }

    // 映射到后端 IrAction 枚举
    const actionMap: Record<string, string> = {
      approve: 'approved',
      modify: 'modified',
      reject: 'rejected',
    };

    // approve / modify 时如果是会议纪要 → 让 IR 选关联投资人
    let investorIds: number[] | null = null;
    if (['approve', 'modify'].includes(action) && msg.taskType === 'meeting_minutes') {
      investorIds = await this._pickInvestorsForReview();
      if (investorIds === null) return; // 用户取消整个流程
    }

    try {
      const body: any = { action: actionMap[action] };
      if (action === 'modify' && final !== undefined) {
        body.final = final;
      }
      if (investorIds && investorIds.length > 0) {
        body.investor_ids = investorIds;
      }
      await api.post(`/api/agent/${msg.threadId}/review`, body);

      // reject 立即更新 UI（approve/modify 等 WS done 推回）
      if (action === 'reject') {
        this._replaceMessage(msg.id, {
          actions: [],
          showStatus: '已拒绝',
        });
      }
    } catch (err: any) {
      wx.showToast({ title: err?.detail || '提交失败', icon: 'none' });
    }
  },

  // ===== 文件上传 =====

  onClearPending() {
    this.setData({ pendingFile: null });
  },

  onPreviewImage(e: WechatMiniprogram.TouchEvent) {
    const url = e.currentTarget.dataset.url as string;
    if (url) wx.previewImage({ urls: [url], current: url });
  },

  async onPlusTap() {
    const choice = await new Promise<number | null>((resolve) => {
      wx.showActionSheet({
        itemList: ['🎵 会议录音（音频）', '📎 文档（BP / PDF / Word）', '🖼️ 图片'],
        success: (r) => resolve(r.tapIndex),
        fail: () => resolve(null),
      });
    });
    if (choice === null) return;
    if (choice === 0) await this._uploadAndDispatchAudio();
    else if (choice === 1) await this._stagePending('doc');
    else await this._stagePending('image');
  },

  async _uploadToQiniu(filePath: string, name: string, purpose: 'audio' | 'doc' | 'image'): Promise<string> {
    const tokenRes = await api.post<{ token: string; key: string; upload_url: string }>(
      '/api/upload/token', { purpose, filename: name }
    );
    await new Promise<void>((resolve, reject) => {
      wx.uploadFile({
        url: tokenRes.upload_url,
        filePath,
        name: 'file',
        formData: { token: tokenRes.token, key: tokenRes.key },
        success: r => r.statusCode === 200 ? resolve() : reject(new Error(`upload ${r.statusCode}`)),
        fail: err => reject(new Error(err.errMsg || 'upload failed')),
      });
    });
    const signRes = await api.get<{ url: string }>(
      `/api/upload/sign?key=${encodeURIComponent(tokenRes.key)}&expires=86400`
    );
    return signRes.url;
  },

  async _uploadAndDispatchAudio() {
    const fileRes = await new Promise<any>((resolve) => {
      wx.chooseMessageFile({
        count: 1, type: 'file',
        extension: ['mp3', 'm4a', 'wav', 'aac', 'mp4'],
        success: r => resolve(r.tempFiles[0]),
        fail: () => resolve(null),
      });
    });
    if (!fileRes) return;
    if (fileRes.size > 200 * 1024 * 1024) {
      wx.showToast({ title: '文件超过 200MB', icon: 'none' });
      return;
    }
    wx.showLoading({ title: '上传中...', mask: true });
    try {
      const url = await this._uploadToQiniu(fileRes.path, fileRes.name, 'audio');
      wx.hideLoading();
      const userId = `u-${Date.now()}`;
      this._appendMessage({ id: userId, kind: 'user', text: `🎵 ${fileRes.name}` });
      const runRes = await api.post<{ thread_id: string }>('/api/agent/run', {
        task_type: 'meeting_minutes',
        audio_url: url,
      });
      this._subscribeToThread(runRes.thread_id);
    } catch (e: any) {
      wx.hideLoading();
      wx.showToast({ title: e?.message || '上传失败', icon: 'none' });
    }
  },

  async _stagePending(purpose: 'doc' | 'image') {
    let pick: { path: string; name: string; size: number } | null = null;
    if (purpose === 'image') {
      pick = await new Promise((resolve) => {
        wx.chooseImage({
          count: 1,
          success: (r) => {
            const p = r.tempFilePaths[0];
            const f = (r as any).tempFiles?.[0];
            resolve({ path: p, name: f?.name || p.split('/').pop() || 'image', size: f?.size || 0 });
          },
          fail: () => resolve(null),
        });
      });
    } else {
      pick = await new Promise((resolve) => {
        wx.chooseMessageFile({
          count: 1, type: 'file',
          extension: ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'md', 'csv'],
          success: (r) => {
            const f = r.tempFiles[0];
            resolve({ path: f.path, name: f.name, size: f.size });
          },
          fail: () => resolve(null),
        });
      });
    }
    if (!pick) return;
    if (pick.size > 50 * 1024 * 1024) {
      wx.showToast({ title: '文件超过 50MB', icon: 'none' });
      return;
    }
    wx.showLoading({ title: '上传中...', mask: true });
    try {
      const url = await this._uploadToQiniu(pick.path, pick.name, purpose);
      wx.hideLoading();
      this.setData({ pendingFile: { url, name: pick.name, purpose } });
      wx.showToast({ title: '已附加，发送指令', icon: 'success', duration: 1500 });
    } catch (e: any) {
      wx.hideLoading();
      wx.showToast({ title: e?.message || '上传失败', icon: 'none' });
    }
  },

  onModalClose() {
    this.setData({ modalVisible: false });
  },

  async onModalAction(e: WechatMiniprogram.CustomEvent<{ action: string; final?: string }>) {
    const { action, final } = e.detail;
    const threadId = this.data.modalThreadId;
    if (!threadId) return;

    const actionMap: Record<string, string> = {
      approve: 'approved',
      reject: 'rejected',
      modify: 'modified',
      modify_and_approve: 'approved',
    };

    // 通过类动作 + 会议纪要 → 让 IR 选关联投资人
    let investorIds: number[] | null = null;
    if (['approve', 'modify', 'modify_and_approve'].includes(action)
        && this.data.modalTaskType === 'meeting_minutes') {
      investorIds = await this._pickInvestorsForReview();
      if (investorIds === null) return;
    }

    try {
      const body: any = { action: actionMap[action] };
      if (final) body.final = final;
      if (investorIds && investorIds.length > 0) body.investor_ids = investorIds;
      await api.post(`/api/agent/${threadId}/review`, body);
      this.setData({ modalVisible: false });
    } catch (err: any) {
      wx.showToast({ title: err?.detail || '提交失败', icon: 'none' });
    }
  },

  /** meeting_minutes 审核时让 IR 关联投资人；返回 null 表示用户取消整个 review。 */
  async _pickInvestorsForReview(): Promise<number[] | null> {
    let investors: { id: number; name: string; agency?: string }[] = [];
    try {
      investors = await api.get<any[]>('/api/investors?limit=8', { silent: true });
    } catch (_e) { investors = []; }

    if (!investors.length) {
      const r = await new Promise<boolean>((resolve) => {
        wx.showModal({
          title: '没有可关联投资人',
          content: '你的投资人库为空，本次将作为「无关联」纪要归档。继续？',
          confirmText: '继续', cancelText: '返回',
          success: (rr) => resolve(rr.confirm),
          fail: () => resolve(false),
        });
      });
      return r ? [] : null;
    }

    const items = investors.map((i) => `${i.name}${i.agency ? ' · ' + i.agency : ''}`);
    items.push('— 暂不关联 —');
    const tapIndex = await new Promise<number | null>((resolve) => {
      wx.showActionSheet({
        itemList: items,
        success: (r) => resolve(r.tapIndex),
        fail: () => resolve(null),
      });
    });
    if (tapIndex === null) return null; // 取消
    if (tapIndex === items.length - 1) return []; // 暂不关联
    return [investors[tapIndex].id];
  },
});

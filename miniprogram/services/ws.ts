/**
 * WebSocket 管理（单例）。
 * - 订阅 thread_id 的事件流
 * - 自动重连（1s/3s/8s 退避）
 * - 3 次失败后调 GET /api/agent/{thread_id}/state 拉快照
 * - 收到 done/error 主动 close，不重连
 */

import * as storage from '../utils/storage';
import { api } from './api';

const app = getApp<IAppOption>();

export type WSEvent =
  | { type: 'node_done'; node: string }
  | { type: 'waiting_review'; draft: string; task_type: string }
  | { type: 'done'; final?: string; ir_action?: string }
  | { type: 'error'; message: string }
  | { type: 'snapshot'; status: string; draft?: string; final?: string; current_node?: string; error?: string };

type EventHandler = (event: WSEvent) => void;

const RECONNECT_DELAYS = [1000, 3000, 8000]; // ms

interface Subscription {
  threadId: string;
  socketTask?: WechatMiniprogram.SocketTask;
  handler: EventHandler;
  reconnectAttempt: number;
  reconnectTimer?: number;
  closed: boolean;
}

class WSManager {
  private subs = new Map<string, Subscription>();

  /**
   * 订阅一个 thread_id 的事件流。同一个 thread 多次 subscribe 会替换 handler。
   */
  subscribe(threadId: string, handler: EventHandler): void {
    // 如果已有订阅，先 unsubscribe
    if (this.subs.has(threadId)) {
      this.unsubscribe(threadId);
    }
    const sub: Subscription = {
      threadId,
      handler,
      reconnectAttempt: 0,
      closed: false,
    };
    this.subs.set(threadId, sub);
    this._connect(sub);
  }

  unsubscribe(threadId: string): void {
    const sub = this.subs.get(threadId);
    if (!sub) return;
    sub.closed = true;
    if (sub.reconnectTimer) {
      clearTimeout(sub.reconnectTimer);
    }
    if (sub.socketTask) {
      try {
        sub.socketTask.close({ code: 1000 });
      } catch {/* ignore */}
    }
    this.subs.delete(threadId);
  }

  private _connect(sub: Subscription): void {
    if (sub.closed) return;

    const jwt = storage.get<string>('mro:jwt');
    if (!jwt) {
      sub.handler({ type: 'error', message: 'no auth token' });
      this.unsubscribe(sub.threadId);
      return;
    }

    const wsUrl = app.globalData.apiBase
      .replace(/^https/, 'wss')
      .replace(/^http/, 'ws')
      + `/api/agent/ws/${sub.threadId}?token=${encodeURIComponent(jwt)}`;

    const socketTask = wx.connectSocket({ url: wsUrl });
    sub.socketTask = socketTask;

    socketTask.onOpen(() => {
      sub.reconnectAttempt = 0; // 重置
    });

    socketTask.onMessage((res) => {
      try {
        const event = JSON.parse(res.data as string) as WSEvent;
        sub.handler(event);

        // done/error 主动断开，不重连
        if (event.type === 'done' || event.type === 'error') {
          sub.closed = true;
          this.subs.delete(sub.threadId);
        }
      } catch (e) {
        console.error('WS parse error', e);
      }
    });

    socketTask.onClose(() => {
      if (sub.closed) return;
      this._scheduleReconnect(sub);
    });

    socketTask.onError(() => {
      // onClose 也会触发，避免重复处理
    });
  }

  private _scheduleReconnect(sub: Subscription): void {
    if (sub.closed) return;

    if (sub.reconnectAttempt >= RECONNECT_DELAYS.length) {
      // 3 次失败 → 拉快照
      this._fallbackToSnapshot(sub);
      return;
    }

    const delay = RECONNECT_DELAYS[sub.reconnectAttempt];
    sub.reconnectAttempt += 1;
    sub.reconnectTimer = setTimeout(() => {
      this._connect(sub);
    }, delay) as unknown as number;
  }

  private async _fallbackToSnapshot(sub: Subscription): Promise<void> {
    try {
      const snap = await api.get<{
        status: string;
        draft?: string;
        final?: string;
        ir_action?: string;
        current_node?: string;
        error?: string;
      }>(`/api/agent/${sub.threadId}/state`, { silent: true });

      sub.handler({ type: 'snapshot', ...snap });

      // 如果 snapshot 是 done/error，直接清理
      if (snap.status === 'done' || snap.status === 'error') {
        this.subs.delete(sub.threadId);
        return;
      }

      // 如果 waiting_review/running，重置 reconnectAttempt 再连一次
      sub.reconnectAttempt = 0;
      this._connect(sub);
    } catch (e) {
      sub.handler({ type: 'error', message: '连接断开，请重试' });
      this.subs.delete(sub.threadId);
    }
  }
}

export const wsManager = new WSManager();

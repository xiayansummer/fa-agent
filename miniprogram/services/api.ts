/**
 * 统一 API 调用层。
 * - 自动附加 JWT
 * - 401 → 清 jwt → 跳 splash
 * - 5xx → toast "服务器繁忙"
 * - 网络错误 → toast "网络异常"
 */

import * as storage from '../utils/storage';

const app = getApp<IAppOption>();

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  data?: any;
  headers?: Record<string, string>;
  silent?: boolean;  // 不显示 toast（用于状态查询等）
}

interface ApiError extends Error {
  code: number;
  detail: string;
}

export async function request<T = any>(path: string, opts: RequestOptions = {}): Promise<T> {
  const jwt = storage.get<string>('mro:jwt');
  const url = `${app.globalData.apiBase}${path}`;

  return new Promise<T>((resolve, reject) => {
    wx.request({
      url,
      method: opts.method || 'GET',
      data: opts.data,
      header: {
        'Content-Type': 'application/json',
        ...(jwt ? { Authorization: `Bearer ${jwt}` } : {}),
        ...(opts.headers || {}),
      },
      success(res) {
        const code = res.statusCode;
        const body = res.data as any;

        if (code >= 200 && code < 300) {
          resolve(body as T);
          return;
        }

        // 401：token 失效 → 清 + 跳 splash
        if (code === 401) {
          storage.del('mro:jwt');
          if (!opts.silent) {
            wx.showToast({ title: '登录已过期', icon: 'none' });
          }
          wx.reLaunch({ url: '/pages/splash/index' });
          reject(makeError(401, body?.detail || '未授权'));
          return;
        }

        // 403：权限不足
        if (code === 403) {
          if (!opts.silent) {
            wx.showToast({ title: body?.detail || '无权限', icon: 'none' });
          }
          reject(makeError(403, body?.detail || '无权限'));
          return;
        }

        // 4xx 业务错误（如 422、404、400）
        if (code >= 400 && code < 500) {
          if (!opts.silent) {
            const detail = typeof body?.detail === 'string' ? body.detail : '请求参数错误';
            wx.showToast({ title: detail, icon: 'none' });
          }
          reject(makeError(code, body?.detail || `HTTP ${code}`));
          return;
        }

        // 5xx 服务端错误
        if (!opts.silent) {
          wx.showToast({ title: '服务器繁忙', icon: 'none' });
        }
        reject(makeError(code, body?.detail || `HTTP ${code}`));
      },
      fail(err) {
        if (!opts.silent) {
          wx.showToast({ title: '网络异常，请重试', icon: 'none' });
        }
        reject(makeError(-1, err.errMsg || '网络异常'));
      },
    });
  });
}

function makeError(code: number, detail: string): ApiError {
  const err = new Error(detail) as ApiError;
  err.code = code;
  err.detail = detail;
  return err;
}

// 便捷方法
export const api = {
  get: <T = any>(path: string, opts?: Omit<RequestOptions, 'method'>) =>
    request<T>(path, { ...opts, method: 'GET' }),
  post: <T = any>(path: string, data?: any, opts?: Omit<RequestOptions, 'method' | 'data'>) =>
    request<T>(path, { ...opts, method: 'POST', data }),
  put: <T = any>(path: string, data?: any, opts?: Omit<RequestOptions, 'method' | 'data'>) =>
    request<T>(path, { ...opts, method: 'PUT', data }),
  del: <T = any>(path: string, opts?: Omit<RequestOptions, 'method'>) =>
    request<T>(path, { ...opts, method: 'DELETE' }),
};

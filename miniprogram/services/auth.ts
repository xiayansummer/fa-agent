/**
 * 认证：登录、绑定手机号、退出。
 */

import { api } from './api';
import * as storage from '../utils/storage';

export interface LoginResponse {
  token: string;
  ir_id: number;
  name: string;
  role: string;
}

export interface NeedBindingResponse {
  need_phone_binding: true;
  login_session: string;
}

export interface CurrentUser {
  id: number;
  name: string;
  phone?: string;
  role: string;
  wechat_openid?: string;
  tencent_bound: boolean;
}

/**
 * 走 wx.login → POST /api/auth/login。
 * 返回 LoginResponse（已绑）或 NeedBindingResponse（需要绑手机号）
 */
export async function login(): Promise<LoginResponse | NeedBindingResponse> {
  const code = await new Promise<string>((resolve, reject) => {
    wx.login({
      success(r) { resolve(r.code); },
      fail(err) { reject(new Error(err.errMsg)); },
    });
  });
  const result = await api.post<LoginResponse | NeedBindingResponse>('/api/auth/login', { code });

  if ('token' in result) {
    storage.set('mro:jwt', result.token);
    storage.set('mro:user', { id: result.ir_id, name: result.name, role: result.role });
  }
  return result;
}

/**
 * 绑定手机号：POST /api/auth/bind_phone
 */
export async function bindPhone(
  loginSession: string,
  encryptedData: string,
  iv: string,
): Promise<LoginResponse> {
  const result = await api.post<LoginResponse>('/api/auth/bind_phone', {
    login_session: loginSession,
    encryptedData,
    iv,
  });
  storage.set('mro:jwt', result.token);
  storage.set('mro:user', { id: result.ir_id, name: result.name, role: result.role });
  return result;
}

/**
 * 退出登录：清 storage + 跳 splash
 */
export function logout(): void {
  storage.del('mro:jwt');
  storage.del('mro:user');
  wx.reLaunch({ url: '/pages/splash/index' });
}

/**
 * 缓存版获取当前用户信息（先看 storage，没有再调 API）
 */
let _cachedUser: CurrentUser | null = null;

export async function getCurrentUser(forceFresh = false): Promise<CurrentUser> {
  if (!forceFresh && _cachedUser) return _cachedUser;
  _cachedUser = await api.get<CurrentUser>('/api/me');
  return _cachedUser;
}

export function clearCachedUser(): void {
  _cachedUser = null;
}

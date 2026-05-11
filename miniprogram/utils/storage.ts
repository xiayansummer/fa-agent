/**
 * 包装 wx storage，自动 JSON serialization。
 */

export function get<T>(key: string): T | null {
  try {
    const val = wx.getStorageSync(key);
    if (val === '' || val === undefined) return null;
    if (typeof val === 'string' && (val.startsWith('{') || val.startsWith('['))) {
      return JSON.parse(val) as T;
    }
    return val as T;
  } catch {
    return null;
  }
}

export function set<T>(key: string, value: T): void {
  const serialized = typeof value === 'string' ? value : JSON.stringify(value);
  wx.setStorageSync(key, serialized);
}

export function del(key: string): void {
  wx.removeStorageSync(key);
}

import * as storage from './utils/storage';
import { appConfig } from './config/env';

App<IAppOption>({
  globalData: {
    apiBase: appConfig.apiBase,
    env: appConfig.env,
  },
  onLaunch() {
    // 检查是否已登录
    const jwt = storage.get<string>('mro:jwt');
    if (!jwt) {
      // 跳启动页（仅首次或未登录）
      wx.reLaunch({ url: '/pages/splash/index' });
    }
  },
});

interface IAppOption {
  globalData: {
    apiBase: string;
    env: string;
  };
}

App<IAppOption>({
  globalData: {
    apiBase: 'https://agentapi.investarget.com',
  },
  onLaunch() {
    // 检查是否已登录
    const jwt = wx.getStorageSync('mro:jwt');
    if (!jwt) {
      // 跳启动页（仅首次或未登录）
      wx.reLaunch({ url: '/pages/splash/index' });
    }
  },
});

interface IAppOption {
  globalData: {
    apiBase: string;
  };
}

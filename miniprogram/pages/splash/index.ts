Page({
  onEnter() {
    // F2 实现：wx.login → POST /api/auth/login
    wx.switchTab({ url: '/pages/calendar/index' });
  },
});

interface PageData {
  url: string;
}

Page<PageData, {}>({
  data: {
    url: '',
  },

  onLoad(opts: { url?: string; title?: string }) {
    if (!opts.url) {
      wx.showToast({ title: '缺少 URL', icon: 'none' });
      setTimeout(() => wx.navigateBack(), 800);
      return;
    }
    const url = decodeURIComponent(opts.url);
    this.setData({ url });
    if (opts.title) {
      wx.setNavigationBarTitle({ title: decodeURIComponent(opts.title) });
    }
  },

  onError(e: WechatMiniprogram.CustomEvent) {
    console.error('webview load error', e.detail);
  },
});

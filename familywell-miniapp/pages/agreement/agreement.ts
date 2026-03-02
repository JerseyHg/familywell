/**
 * pages/agreement/agreement.ts — 用户服务协议 / 隐私政策
 * 参数: type = 'service' | 'privacy'
 */
Page({
  data: {
    type: 'service' as 'service' | 'privacy',
  },

  onLoad(options: any) {
    const type = options.type || 'service'
    this.setData({ type })

    wx.setNavigationBarTitle({
      title: type === 'privacy' ? '隐私政策' : '用户服务协议',
    })
  },
})

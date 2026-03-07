App<IAppOption>({
  globalData: {
    token: '',
    userInfo: null,
    chatInitQuestion: '',
  },

  onLaunch() {
    // 临时开启调试
    wx.setEnableDebug({ enableDebug: true })

    const token = wx.getStorageSync('token')
    if (token) {
      this.globalData.token = token
      this.globalData.userInfo = wx.getStorageSync('user')
    }
  },

  setToken(token: string, userInfo: any) {
    this.globalData.token = token
    this.globalData.userInfo = userInfo
    wx.setStorageSync('token', token)
    wx.setStorageSync('user', userInfo)
  },

  clearToken() {
    this.globalData.token = ''
    this.globalData.userInfo = null
    wx.removeStorageSync('token')
    wx.removeStorageSync('user')
    // 清除所有业务缓存
    try {
      const { clearAllCache } = require('./services/cache')
      clearAllCache()
    } catch { /* ignore */ }
  },

  isLoggedIn(): boolean {
    return !!this.globalData.token
  },
})

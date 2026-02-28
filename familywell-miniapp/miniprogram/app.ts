App<IAppOption>({
  globalData: {
    token: '',
    userInfo: null,
    chatInitQuestion: '',
  },

  onLaunch() {
    const token = wx.getStorageSync('token')
    if (token) {
      this.globalData.token = token
      this.globalData.userInfo = wx.getStorageSync('userInfo')
    }
  },

  setToken(token: string, userInfo: any) {
    this.globalData.token = token
    this.globalData.userInfo = userInfo
    wx.setStorageSync('token', token)
    wx.setStorageSync('userInfo', userInfo)
  },

  clearToken() {
    this.globalData.token = ''
    this.globalData.userInfo = null
    wx.removeStorageSync('token')
    wx.removeStorageSync('userInfo')
  },

  isLoggedIn(): boolean {
    return !!this.globalData.token
  },
})

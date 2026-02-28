import { authApi } from '../../services/api'

Page({
  data: {
    mode: 'login' as 'login' | 'register',
    username: '',
    password: '',
    nickname: '',
    loading: false,
  },

  switchMode(e: any) {
    this.setData({ mode: e.currentTarget.dataset.mode })
  },

  onUsernameInput(e: any) {
    this.setData({ username: e.detail.value })
  },

  onPasswordInput(e: any) {
    this.setData({ password: e.detail.value })
  },

  onNicknameInput(e: any) {
    this.setData({ nickname: e.detail.value })
  },

  async onSubmit() {
    const { mode, username, password, nickname } = this.data

    if (!username.trim() || !password.trim()) {
      wx.showToast({ title: '请填写用户名和密码', icon: 'none' })
      return
    }

    if (mode === 'register' && !nickname.trim()) {
      wx.showToast({ title: '请填写昵称', icon: 'none' })
      return
    }

    this.setData({ loading: true })

    try {
      let res: any

      if (mode === 'login') {
        res = await authApi.login({ username, password })
      } else {
        res = await authApi.register({ username, password, nickname })
      }

      // 存储 token 和用户信息
      wx.setStorageSync('token', res.access_token)
      wx.setStorageSync('user', res.user)

      wx.showToast({ title: mode === 'login' ? '登录成功' : '注册成功', icon: 'success' })

      // 跳转首页
      setTimeout(() => {
        wx.switchTab({ url: '/pages/home/home' })
      }, 500)

    } catch (err: any) {
      wx.showToast({
        title: err.message || '操作失败',
        icon: 'none',
      })
    } finally {
      this.setData({ loading: false })
    }
  },
})

/**
 * pages/login/login.ts — 登录页
 * ═══════════════════════════════════════
 * ★ 审核整改：登录后返回上一页（而不是固定跳转首页）
 * ★ 隐私协议 checkbox + wx.requirePrivacyAuthorize
 */
import { authApi } from '../../services/api'

Page({
  data: {
    mode: 'wechat' as 'wechat' | 'account-login' | 'account-register',
    username: '',
    password: '',
    nickname: '',
    loading: false,
    wxLoading: false,
    privacyAgreed: false,
  },

  noop() {},

  // ── 隐私协议 ──

  onTogglePrivacy() {
    this.setData({ privacyAgreed: !this.data.privacyAgreed })
  },

  onOpenPrivacy() {
    if (typeof wx.openPrivacyContract === 'function') {
      wx.openPrivacyContract({
        fail: () => {
          wx.showToast({ title: '暂时无法打开', icon: 'none' })
        },
      })
    } else {
      wx.showToast({ title: '请升级微信版本查看', icon: 'none' })
    }
  },

  _ensurePrivacy(): Promise<boolean> {
    return new Promise((resolve) => {
      if (typeof wx.requirePrivacyAuthorize === 'function') {
        wx.requirePrivacyAuthorize({
          success: () => resolve(true),
          fail: () => {
            wx.showToast({ title: '需要同意隐私协议才能使用', icon: 'none' })
            resolve(false)
          },
        })
      } else {
        resolve(true)
      }
    })
  },

  // ── 模式切换 ──

  switchToAccount() {
    this.setData({ mode: 'account-login' })
  },

  switchToWechat() {
    this.setData({ mode: 'wechat' })
  },

  switchMode(e: any) {
    this.setData({ mode: e.currentTarget.dataset.mode })
  },

  onUsernameInput(e: any) { this.setData({ username: e.detail.value }) },
  onPasswordInput(e: any) { this.setData({ password: e.detail.value }) },
  onNicknameInput(e: any) { this.setData({ nickname: e.detail.value }) },

  /**
   * ★ 登录成功后的跳转逻辑
   * - 新用户 → 引导页
   * - 老用户 → 返回上一页（如果有页面栈）或跳转首页
   */
  _navigateAfterLogin(isNew: boolean) {
    if (isNew) {
      wx.redirectTo({ url: '/pages/onboarding/onboarding' })
      return
    }

    // ★ 审核整改：优先 navigateBack 回到首页
    const pages = getCurrentPages()
    if (pages.length > 1) {
      wx.navigateBack()
    } else {
      wx.switchTab({ url: '/pages/home/home' })
    }
  },

  // ── 微信登录 ──

  async onWxLogin() {
    if (!this.data.privacyAgreed) {
      wx.showToast({ title: '请先同意隐私协议', icon: 'none' })
      return
    }
    if (this.data.wxLoading) return
    this.setData({ wxLoading: true })

    try {
      const privacyOk = await this._ensurePrivacy()
      if (!privacyOk) {
        this.setData({ wxLoading: false })
        return
      }

      const loginRes = await new Promise<WechatMiniprogram.LoginSuccessCallbackResult>(
        (resolve, reject) => {
          wx.login({ success: resolve, fail: reject })
        }
      )

      if (!loginRes.code) {
        throw new Error('获取微信 code 失败')
      }

      const res: any = await authApi.wxLogin({ code: loginRes.code })

      wx.setStorageSync('token', res.access_token)
      wx.setStorageSync('user', res.user)

      wx.showToast({ title: '登录成功', icon: 'success' })

      setTimeout(() => {
        this._navigateAfterLogin(res.user.is_new)
      }, 500)

    } catch (err: any) {
      console.error('WeChat login failed:', err)
      wx.showToast({ title: err.message || '微信登录失败', icon: 'none' })
    } finally {
      this.setData({ wxLoading: false })
    }
  },

  // ── 账号密码登录/注册 ──

  async onSubmit() {
    if (!this.data.privacyAgreed) {
      wx.showToast({ title: '请先同意隐私协议', icon: 'none' })
      return
    }

    const { mode, username, password, nickname } = this.data

    if (!username.trim() || !password.trim()) {
      wx.showToast({ title: '请填写用户名和密码', icon: 'none' })
      return
    }

    if (mode === 'account-register' && !nickname.trim()) {
      wx.showToast({ title: '请填写昵称', icon: 'none' })
      return
    }

    this.setData({ loading: true })

    try {
      const privacyOk = await this._ensurePrivacy()
      if (!privacyOk) {
        this.setData({ loading: false })
        return
      }

      let res: any

      if (mode === 'account-login') {
        res = await authApi.login({ username, password })
      } else {
        res = await authApi.register({ username, password, nickname })
      }

      wx.setStorageSync('token', res.access_token)
      wx.setStorageSync('user', res.user)

      wx.showToast({
        title: mode === 'account-login' ? '登录成功' : '注册成功',
        icon: 'success',
      })

      setTimeout(() => {
        this._navigateAfterLogin(mode === 'account-register')
      }, 500)

    } catch (err: any) {
      wx.showToast({ title: err.message || '操作失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  goAgreement(e: any) {
    const type = e.currentTarget.dataset.type || 'service'
    wx.navigateTo({ url: `/pages/agreement/agreement?type=${type}` })
  },
})

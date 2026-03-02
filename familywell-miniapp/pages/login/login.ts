/**
 * pages/login/login.ts — 登录页
 * ═══════════════════════════════════════
 * ★ 新增：隐私协议 checkbox + wx.requirePrivacyAuthorize
 *   登录时一次性完成隐私确认，后续使用不再弹窗
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
    privacyAgreed: false,   // ★ 隐私协议是否勾选
  },

  noop() {},

  // ── 隐私协议 ──

  onTogglePrivacy() {
    this.setData({ privacyAgreed: !this.data.privacyAgreed })
  },

  onOpenPrivacy() {
    // 调用微信官方隐私协议弹窗（展示你在 mp 后台配置的隐私保护指引）
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

  /**
   * ★ 统一隐私确认：调用 wx.requirePrivacyAuthorize
   * 成功后微信不再弹系统级隐私确认框
   */
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
        // 低版本基础库不需要隐私确认
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

  // ── 微信登录 ──

  async onWxLogin() {
    if (!this.data.privacyAgreed) {
      wx.showToast({ title: '请先同意隐私协议', icon: 'none' })
      return
    }
    if (this.data.wxLoading) return
    this.setData({ wxLoading: true })

    try {
      // ★ 先完成微信隐私确认
      const privacyOk = await this._ensurePrivacy()
      if (!privacyOk) {
        this.setData({ wxLoading: false })
        return
      }

      // 获取微信 code
      const loginRes = await new Promise<WechatMiniprogram.LoginSuccessCallbackResult>(
        (resolve, reject) => {
          wx.login({ success: resolve, fail: reject })
        }
      )

      if (!loginRes.code) {
        throw new Error('获取微信 code 失败')
      }

      // 调用后端
      const res: any = await authApi.wxLogin({ code: loginRes.code })

      wx.setStorageSync('token', res.access_token)
      wx.setStorageSync('user', res.user)

      wx.showToast({ title: '登录成功', icon: 'success' })

      setTimeout(() => {
        if (res.user.is_new) {
          wx.redirectTo({ url: '/pages/onboarding/onboarding' })
        } else {
          wx.switchTab({ url: '/pages/home/home' })
        }
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
      // ★ 先完成隐私确认
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
        if (mode === 'account-register') {
          wx.redirectTo({ url: '/pages/onboarding/onboarding' })
        } else {
          wx.switchTab({ url: '/pages/home/home' })
        }
      }, 500)

    } catch (err: any) {
      wx.showToast({ title: err.message || '操作失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  /** ★ 跳转到服务协议 / 隐私政策页面 */
  goAgreement(e: any) {
    const type = e.currentTarget.dataset.type || 'service'
    wx.navigateTo({ url: `/pages/agreement/agreement?type=${type}` })
  },
})

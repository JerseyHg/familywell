/**
 * pages/login/login.ts — 登录页
 * ─────────────────────────────────
 * [P1-2] 新增微信一键登录
 *
 * 改造思路：
 * - 默认展示"微信一键登录"按钮（推荐方式）
 * - 底部保留"使用账号密码登录"入口（备用方式）
 * - 微信登录流程：wx.login() → code → 后端换 openid → 返回 JWT
 */
import { authApi } from '../../services/api'

Page({
  data: {
    // 登录模式：'wechat' | 'account-login' | 'account-register'
    mode: 'wechat' as string,

    // 账号密码表单
    username: '',
    password: '',
    nickname: '',
    loading: false,

    // 微信登录
    wxLoading: false,
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

  // ── 账号密码表单 ──
  onUsernameInput(e: any) {
    this.setData({ username: e.detail.value })
  },

  onPasswordInput(e: any) {
    this.setData({ password: e.detail.value })
  },

  onNicknameInput(e: any) {
    this.setData({ nickname: e.detail.value })
  },

  // ── [P1-2] 微信一键登录 ──
  async onWxLogin() {
    if (this.data.wxLoading) return
    this.setData({ wxLoading: true })

    try {
      // 1. 获取微信 code
      const loginRes = await new Promise<WechatMiniprogram.LoginSuccessCallbackResult>(
        (resolve, reject) => {
          wx.login({
            success: resolve,
            fail: reject,
          })
        }
      )

      if (!loginRes.code) {
        throw new Error('获取微信 code 失败')
      }

      // 2. （可选）获取微信头像昵称 — 需用户主动点击 button
      // 这里先不获取，用户可以在建档时补充

      // 3. 调用后端微信登录接口
      const res: any = await authApi.wxLogin({
        code: loginRes.code,
      })

      // 4. 存储 token
      wx.setStorageSync('token', res.access_token)
      wx.setStorageSync('user', res.user)

      wx.showToast({ title: '登录成功', icon: 'success' })

      setTimeout(() => {
        if (res.user.is_new) {
          // 新用户 → 引导填资料
          wx.redirectTo({ url: '/pages/onboarding/onboarding' })
        } else {
          // 老用户 → 直接进首页
          wx.switchTab({ url: '/pages/home/home' })
        }
      }, 500)

    } catch (err: any) {
      console.error('WeChat login failed:', err)
      wx.showToast({
        title: err.message || '微信登录失败',
        icon: 'none',
      })
    } finally {
      this.setData({ wxLoading: false })
    }
  },

  // ── 账号密码登录/注册（保持原逻辑）──
  async onSubmit() {
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
      wx.showToast({
        title: err.message || '操作失败',
        icon: 'none',
      })
    } finally {
      this.setData({ loading: false })
    }
  },
})

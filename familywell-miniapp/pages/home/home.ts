import { homeApi, medsApi, profileApi } from '../../services/api'
import { batchUpload, pollBatchAIStatus } from '../../services/upload'

Page({
  data: {
    profile: { nickname: '', age: null, tags: [] as string[] },
    pendingTasks: [] as any[],
    aiTip: '',
    recentActivity: [] as any[],
    alertCount: 0,
    prompts: [
      { icon: '🍽️', text: '过去7天饮食情况' },
      { icon: '💉', text: '血压最近趋势怎样' },
      { icon: '💊', text: '这周药吃齐了吗' },
      { icon: '📋', text: '最近身体怎么样' },
    ],
    greeting: '',

    // 语音弹窗
    showVoiceModal: false,
    voiceText: '',
    voiceSubmitting: false,
  },

  onLoad() {
    const hour = new Date().getHours()
    let greeting = '你好'
    if (hour < 6) greeting = '夜深了'
    else if (hour < 12) greeting = '早上好'
    else if (hour < 18) greeting = '下午好'
    else greeting = '晚上好'
    this.setData({ greeting })
  },

  onShow() {
    const token = wx.getStorageSync('token')
    if (!token) {
      wx.redirectTo({ url: '/pages/login/login' })
      return
    }

    this.getTabBar()?.setData({ active: 0 })
    this.checkOnboarding()
    this.loadHomeData()
  },

  onPullDownRefresh() {
    this.loadHomeData().then(() => wx.stopPullDownRefresh())
  },

  /** 检查是否完成引导，未完成则跳转 */
  async checkOnboarding() {
    try {
      const profile: any = await profileApi.get()
      if (profile && !profile.onboarding_completed) {
        wx.redirectTo({ url: '/pages/onboarding/onboarding' })
      }
    } catch (e) {
      console.warn('Profile check failed:', e)
    }
  },

  async loadHomeData() {
    try {
      const res: any = await homeApi.getData()
      this.setData({
        profile: res.profile,
        pendingTasks: res.pending_tasks || [],
        aiTip: res.ai_tip || '',
        recentActivity: res.recent_activity || [],
        alertCount: res.alert_count || 0,
      })
    } catch (err) {
      console.error('Failed to load home data:', err)
    }
  },

  // ── Actions ──

  onUpload() {
    batchUpload().then(({ recordIds }) => {
      pollBatchAIStatus(
        recordIds,
        () => {
          wx.showToast({ title: '识别完成', icon: 'success' })
          this.loadHomeData()
        },
        () => wx.showToast({ title: '部分识别失败', icon: 'none' }),
      )
    }).catch(() => {})
  },

  onPunchTask(e: any) {
    const taskId = e.currentTarget.dataset.id
    medsApi.completeTask(taskId).then(() => {
      wx.showToast({ title: '打卡成功', icon: 'success' })
      this.loadHomeData()
    })
  },

  onPromptTap(e: any) {
    const text = e.currentTarget.dataset.text
    wx.switchTab({
      url: '/pages/chat/chat',
      success: () => {
        getApp().globalData.chatInitQuestion = text
      },
    })
  },

  // ── 语音记录 ──

  noop() {},

  onVoiceAdd() {
    this.setData({ showVoiceModal: true, voiceText: '' })
  },

  hideVoiceModal() {
    this.setData({ showVoiceModal: false })
  },

  onVoiceTextInput(e: any) {
    this.setData({ voiceText: e.detail.value })
  },

  async onSubmitVoice() {
    const text = this.data.voiceText.trim()
    if (!text) {
      wx.showToast({ title: '请输入内容', icon: 'none' })
      return
    }

    // 防重复提交
    if (this.data.voiceSubmitting) return
    this.setData({ voiceSubmitting: true })

    // ★ 立即关闭弹窗
    this.setData({ showVoiceModal: false, voiceText: '' })

    // ★ 乐观更新：先在最近动态里插一条"处理中"
    const optimistic = {
      id: `temp_${Date.now()}`,
      category: 'other',
      title: text.slice(0, 30) + (text.length > 30 ? '…' : ''),
      date: `${new Date().getMonth() + 1}/${new Date().getDate()}`,
      ai_status: 'processing',
    }
    this.setData({
      recentActivity: [optimistic, ...this.data.recentActivity].slice(0, 5),
    })
    wx.showToast({ title: '已提交，AI 处理中', icon: 'none', duration: 1500 })

    // 后台处理
    try {
      const res: any = await medsApi.voiceAdd(text)

      // 成功 → 轻提示 + 刷新真实数据
      const type = res.type || 'unknown'
      const icons: Record<string, string> = { medication: '💊', food: '🍽️', vitals: '❤️', symptom: '📝' }
      wx.showToast({ title: `${icons[type] || '✅'} ${res.summary || '记录成功'}`, icon: 'none', duration: 2000 })
      this.loadHomeData()
    } catch (e) {
      // 失败 → 更新状态为 failed
      const updated = this.data.recentActivity.map((item: any) =>
        item.id === optimistic.id ? { ...item, ai_status: 'failed', _voiceText: text } : item
      )
      this.setData({ recentActivity: updated })
      wx.showToast({ title: '处理失败，点击重试', icon: 'none' })
      console.error('Voice add failed:', e)
    } finally {
      this.setData({ voiceSubmitting: false })
    }
  },

  /** 重试失败的语音记录 */
  onRetryVoice(e: any) {
    const text = e.currentTarget.dataset.text
    if (!text) return
    this.setData({ voiceText: text })
    this.onSubmitVoice()
  },
})

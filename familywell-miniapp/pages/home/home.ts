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

    this.setData({ voiceSubmitting: true })
    try {
      const res: any = await medsApi.voiceAdd(text)
      this.setData({ voiceSubmitting: false, showVoiceModal: false })

      const count = res.medications?.length || 0
      const names = (res.medications || []).map((m: any) => m.name).join('、')

      wx.showModal({
        title: `已添加 ${count} 个药物`,
        content: names ? `${names}\n今天的服药提醒已生成` : '未识别到药物信息',
        showCancel: false,
      })

      // 刷新首页数据
      this.loadHomeData()
    } catch (e) {
      this.setData({ voiceSubmitting: false })
      console.error('Voice add failed:', e)
    }
  },
})

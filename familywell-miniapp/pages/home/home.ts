import { homeApi, medsApi } from '../../services/api'
import { chooseAndUpload, pollAIStatus } from '../../services/upload'

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
    // 未登录则跳转登录页
    const token = wx.getStorageSync('token')
    if (!token) {
      wx.redirectTo({ url: '/pages/login/login' })
      return
    }

    this.getTabBar()?.setData({ active: 0 })
    this.loadHomeData()
  },

  onPullDownRefresh() {
    this.loadHomeData().then(() => wx.stopPullDownRefresh())
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
    chooseAndUpload().then(({ recordId }) => {
      pollAIStatus(
        recordId,
        () => {
          wx.showToast({ title: '识别完成', icon: 'success' })
          this.loadHomeData()
        },
        () => wx.showToast({ title: '识别失败', icon: 'none' }),
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
        // 通过全局变量传递初始问题
        getApp().globalData.chatInitQuestion = text
      },
    })
  },
})

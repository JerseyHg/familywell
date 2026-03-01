/**
 * pages/home/home.ts — 首页（完整版）
 * ══════════════════════════════════════════════════════════
 * 包含：首页数据加载、拍照上传、用药打卡、AI 提示词跳转、
 *       语音录入（WechatSI 同声传译插件）、记录详情跳转
 */

import { homeApi, medsApi, profileApi } from '../../services/api'
import { batchUpload, pollBatchAIStatus } from '../../services/upload'

// ── 微信同声传译插件 ──
const plugin = requirePlugin('WechatSI')

// ── 语音识别管理器初始化 ──
function initVoiceRecognizer(page: any) {
  const manager = plugin.getRecordRecognitionManager()

  // 录音开始
  manager.onStart(() => {
    console.log('Voice recording started')
  })

  // 实时识别结果（中间结果，会不断更新）
  manager.onRecognize((res: any) => {
    if (res.result) {
      page.setData({ voiceText: res.result })
    }
  })

  // 录音结束 + 最终识别结果
  manager.onStop((res: any) => {
    page.setData({ isRecording: false })

    // 清除计时器
    if (page._recordTimer) {
      clearInterval(page._recordTimer)
      page._recordTimer = null
    }

    if (res.result) {
      page.setData({ voiceText: res.result })
    } else if (res.retcode !== 0) {
      console.error('Voice recognition failed:', res)
      wx.showToast({ title: '语音识别失败，请重试', icon: 'none' })
    }
  })

  // 错误处理
  manager.onError((err: any) => {
    console.error('Voice recognition error:', err)
    page.setData({ isRecording: false })

    if (page._recordTimer) {
      clearInterval(page._recordTimer)
      page._recordTimer = null
    }

    wx.showToast({ title: '录音出错，请重试', icon: 'none' })
  })

  return manager
}

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
    isRecording: false,
    recordingDuration: 0,
  },

  // 非响应式私有属性
  _voiceManager: null as any,
  _recordTimer: null as any,

  // ════════════════════════════════════════
  //  生命周期
  // ════════════════════════════════════════

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

  // ════════════════════════════════════════
  //  数据加载
  // ════════════════════════════════════════

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

  // ════════════════════════════════════════
  //  拍照上传
  // ════════════════════════════════════════

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

  // ════════════════════════════════════════
  //  用药打卡
  // ════════════════════════════════════════

  onPunchTask(e: any) {
    const taskId = e.currentTarget.dataset.id
    medsApi.completeTask(taskId).then(() => {
      wx.showToast({ title: '打卡成功', icon: 'success' })
      this.loadHomeData()
    })
  },

  // ════════════════════════════════════════
  //  AI 提示词 → 跳转 Chat
  // ════════════════════════════════════════

  onPromptTap(e: any) {
    const text = e.currentTarget.dataset.text
    wx.switchTab({
      url: '/pages/chat/chat',
      success: () => {
        getApp().globalData.chatInitQuestion = text
      },
    })
  },

  // ════════════════════════════════════════
  //  最近动态 → 跳转记录详情
  // ════════════════════════════════════════

  onActivityTap(e: any) {
    const id = e.currentTarget.dataset.id
    if (!id || String(id).startsWith('temp_')) return
    wx.navigateTo({ url: `/pages/record-detail/record-detail?id=${id}` })
  },

  // ════════════════════════════════════════
  //  语音录入（WechatSI 同声传译插件）
  // ════════════════════════════════════════

  noop() {},

  onVoiceAdd() {
    this.setData({
      showVoiceModal: true,
      voiceText: '',
      isRecording: false,
      recordingDuration: 0,
    })

    // 确保录音管理器已初始化
    if (!this._voiceManager) {
      this._voiceManager = initVoiceRecognizer(this)
    }
  },

  hideVoiceModal() {
    // 如果正在录音，先停止
    if (this.data.isRecording && this._voiceManager) {
      this._voiceManager.stop()
    }
    this.setData({ showVoiceModal: false, isRecording: false })
    if (this._recordTimer) {
      clearInterval(this._recordTimer)
      this._recordTimer = null
    }
  },

  onVoiceTextInput(e: any) {
    this.setData({ voiceText: e.detail.value })
  },

  // ── 按住录音 ──
  onRecordStart() {
    if (this.data.isRecording) return

    // 检查录音权限
    wx.authorize({
      scope: 'scope.record',
      success: () => {
        this._startRecording()
      },
      fail: () => {
        wx.showToast({ title: '请授权录音权限', icon: 'none' })
      },
    })
  },

  _startRecording() {
    if (!this._voiceManager) {
      this._voiceManager = initVoiceRecognizer(this)
    }

    this.setData({
      isRecording: true,
      recordingDuration: 0,
      voiceText: '',  // 清空上一次的文字
    })

    // 开始录音 + 识别
    this._voiceManager.start({
      lang: 'zh_CN',  // 中文
      duration: 60000, // 最长 60 秒
    })

    // 计时器（显示录音时长）
    let seconds = 0
    this._recordTimer = setInterval(() => {
      seconds++
      this.setData({ recordingDuration: seconds })

      // 60 秒自动停止
      if (seconds >= 60) {
        this.onRecordStop()
      }
    }, 1000)
  },

  // ── 松开结束录音 ──
  onRecordStop() {
    if (!this.data.isRecording) return

    if (this._voiceManager) {
      this._voiceManager.stop()
    }

    // isRecording 和计时器在 onStop 回调中清理
  },

  // ── 提交语音/文字内容 ──
  async onSubmitVoice() {
    const text = this.data.voiceText.trim()
    if (!text) {
      wx.showToast({ title: '请先说话或输入内容', icon: 'none' })
      return
    }

    // 如果还在录音，先停止
    if (this.data.isRecording) {
      this.onRecordStop()
      // 等一下让 ASR 返回最终结果
      await new Promise(resolve => setTimeout(resolve, 500))
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
      const icons: Record<string, string> = {
        medication: '💊', food: '🍽️', vitals: '❤️', symptom: '📝',
      }
      wx.showToast({
        title: `${icons[type] || '✅'} ${res.summary || '记录成功'}`,
        icon: 'none',
        duration: 2000,
      })
      this.loadHomeData()
    } catch (e) {
      // 失败 → 更新状态为 failed
      const updated = this.data.recentActivity.map((item: any) =>
        item.id === optimistic.id
          ? { ...item, ai_status: 'failed', _voiceText: text }
          : item
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

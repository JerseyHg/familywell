/**
 * pages/home/home.ts — 首页（完整版）
 * ══════════════════════════════════════════════════════════
 * ★ 新增：待确认药物建议（MedicationSuggestion）
 * ★ 语音修复：WechatSI 回调用「属性赋值」（manager.onStop = func）
 * ★ 追加模式：多次录音拼接，方便补充说明
 * ★ 后端支持多类型拆分：一段话里的饮食/用药/指标各自保存
 */

import { homeApi, medsApi, profileApi } from '../../services/api'
import { batchUpload, pollBatchAIStatus } from '../../services/upload'

// ── 微信同声传译插件 ──
const plugin = requirePlugin('WechatSI')

Page({
  data: {
    profile: { nickname: '', age: null, tags: [] as string[] },
    pendingTasks: [] as any[],
    aiTip: '',
    recentActivity: [] as any[],
    alertCount: 0,
    medSuggestions: [] as any[],   // ★ 待确认药物建议
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
  _voiceInited: false,
  _recordTimer: null as any,
  _stopFallbackTimer: null as any,
  _baseText: '',               // ★ 追加模式：之前已确认的文字

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

      // ★ 为 suggestions 添加前端交互状态
      const suggestions = (res.medication_suggestions || []).map((s: any) => ({
        ...s,
        _expanded: false,
        _times: 1,
        _medType: 'long_term',
        _totalDays: 7,
      }))

      this.setData({
        profile: res.profile,
        pendingTasks: res.pending_tasks || [],
        aiTip: res.ai_tip || '',
        recentActivity: res.recent_activity || [],
        alertCount: res.alert_count || 0,
        medSuggestions: suggestions,
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
  //  ★ 药物建议：展开 / 确认 / 忽略
  // ════════════════════════════════════════

  onToggleSuggestion(e: any) {
    const id = e.currentTarget.dataset.id
    const list = this.data.medSuggestions
    const idx = list.findIndex((s: any) => s.id === id)
    if (idx < 0) return
    this.setData({ [`medSuggestions[${idx}]._expanded`]: true })
  },

  onSugTimes(e: any) {
    const { id, times } = e.currentTarget.dataset
    const idx = this.data.medSuggestions.findIndex((s: any) => s.id === Number(id))
    if (idx < 0) return
    this.setData({ [`medSuggestions[${idx}]._times`]: Number(times) })
  },

  onSugType(e: any) {
    const { id, type } = e.currentTarget.dataset
    const idx = this.data.medSuggestions.findIndex((s: any) => s.id === Number(id))
    if (idx < 0) return
    this.setData({ [`medSuggestions[${idx}]._medType`]: type })
  },

  onSugDays(e: any) {
    const id = e.currentTarget.dataset.id
    const idx = this.data.medSuggestions.findIndex((s: any) => s.id === Number(id))
    if (idx < 0) return
    this.setData({ [`medSuggestions[${idx}]._totalDays`]: Number(e.detail.value) || 7 })
  },

  async onConfirmSuggestion(e: any) {
    const id = e.currentTarget.dataset.id
    const sug = this.data.medSuggestions.find((s: any) => s.id === Number(id))
    if (!sug) return

    try {
      await medsApi.confirmSuggestion(Number(id), {
        times_per_day: sug._times,
        med_type: sug._medType,
        total_days: (sug._medType === 'course' || sug._medType === 'temporary') ? sug._totalDays : null,
        dosage: sug.dosage,
      })
      wx.showToast({ title: `已添加「${sug.name}」`, icon: 'success' })
      this.loadHomeData()
    } catch (err: any) {
      wx.showToast({ title: err.message || '确认失败', icon: 'none' })
    }
  },

  async onDismissSuggestion(e: any) {
    const id = e.currentTarget.dataset.id
    const sug = this.data.medSuggestions.find((s: any) => s.id === Number(id))
    try {
      await medsApi.dismissSuggestion(Number(id))
      wx.showToast({ title: `已忽略「${sug?.name || '药物'}」`, icon: 'none' })
      this.loadHomeData()
    } catch (err: any) {
      wx.showToast({ title: err.message || '操作失败', icon: 'none' })
    }
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
  //  语音录入（WechatSI）
  // ════════════════════════════════════════

  onVoiceAdd() {
    this.setData({
      showVoiceModal: true,
      voiceText: '',
      isRecording: false,
      recordingDuration: 0,
    })
    this._baseText = ''
  },

  hideVoiceModal() {
    if (this.data.isRecording) {
      this._voiceManager?.stop?.()
    }
    this.setData({ showVoiceModal: false })
  },

  noop() {},

  onVoiceTextInput(e: any) {
    this.setData({ voiceText: e.detail.value })
  },

  // ── 录音 ──
  onToggleRecord() {
    if (this.data.isRecording) {
      this._stopRecording()
    } else {
      this._startRecording()
    }
  },

  _initVoice() {
    if (this._voiceInited) return
    const manager = plugin.getRecordRecognitionManager()

    manager.onStart = () => {
      console.log('[Voice] started')
      this.setData({ isRecording: true, recordingDuration: 0 })
      this._recordTimer = setInterval(() => {
        this.setData({ recordingDuration: this.data.recordingDuration + 1 })
      }, 1000)
    }

    manager.onRecognize = (res: any) => {
      if (res.result) {
        const combined = this._baseText
          ? this._baseText + '，' + res.result
          : res.result
        this.setData({ voiceText: combined })
      }
    }

    manager.onStop = (res: any) => {
      console.log('[Voice] stopped, result:', res.result)
      clearInterval(this._recordTimer)
      clearTimeout(this._stopFallbackTimer)
      this.setData({ isRecording: false })

      if (res.result) {
        const combined = this._baseText
          ? this._baseText + '，' + res.result
          : res.result
        this.setData({ voiceText: combined })
        this._baseText = combined
      }
    }

    manager.onError = (err: any) => {
      console.error('[Voice] error:', err)
      clearInterval(this._recordTimer)
      clearTimeout(this._stopFallbackTimer)
      this.setData({ isRecording: false })
      wx.showToast({ title: '录音失败，请重试', icon: 'none' })
    }

    this._voiceManager = manager
    this._voiceInited = true
  },

  _startRecording() {
    this._initVoice()
    if (this.data.voiceText && !this._baseText) {
      this._baseText = this.data.voiceText
    }
    this._voiceManager.start({ lang: 'zh_CN' })
    this._stopFallbackTimer = setTimeout(() => {
      if (this.data.isRecording) {
        this._stopRecording()
      }
    }, 55000)
  },

  _stopRecording() {
    clearTimeout(this._stopFallbackTimer)
    this._voiceManager?.stop?.()
  },

  // ── 提交 ──
  async onSubmitVoice() {
    const text = this.data.voiceText.trim()
    if (!text) {
      wx.showToast({ title: '请先录音或输入内容', icon: 'none' })
      return
    }
    if (this.data.voiceSubmitting) return
    this.setData({ voiceSubmitting: true })

    try {
      const res: any = await medsApi.voiceAdd(text)
      const items = res.items || [res]

      const parts: string[] = []
      for (const item of items) {
        if (item.message) parts.push(item.message)
      }
      const msg = parts.join('\n') || '已记录'

      wx.showToast({ title: msg.slice(0, 20), icon: 'success', duration: 2000 })
      this.setData({ showVoiceModal: false, voiceText: '' })
      this._baseText = ''
      this.loadHomeData()
    } catch (err: any) {
      wx.showToast({ title: err.message || '记录失败', icon: 'none' })
    } finally {
      this.setData({ voiceSubmitting: false })
    }
  },

  onRetryVoice(e: any) {
    const text = e.currentTarget.dataset.text
    if (text) {
      this.setData({ showVoiceModal: true, voiceText: text })
      this._baseText = ''
    }
  },
})

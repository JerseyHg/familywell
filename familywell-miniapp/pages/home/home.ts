/**
 * pages/home/home.ts — 首页
 * ═══════════════════════════════════════
 * ★ 语音：多段录音、按住说话、隐藏文字
 * ★ 拍照/录音不再弹隐私确认（已在登录页完成）
 * ★ 打卡乐观更新
 */

import { homeApi, medsApi, profileApi } from '../../services/api'
import { batchUpload, pollBatchAIStatus } from '../../services/upload'

const plugin = requirePlugin('WechatSI')

interface VoiceSegment {
  duration: number
  text: string
}

Page({
  data: {
    profile: { nickname: '', age: null, tags: [] as string[] },
    pendingTasks: [] as any[],
    aiTip: '',
    recentActivity: [] as any[],
    alertCount: 0,
    medSuggestions: [] as any[],
    prompts: [
      { icon: '🍽️', text: '过去7天饮食情况' },
      { icon: '💉', text: '血压最近趋势怎样' },
      { icon: '💊', text: '这周药吃齐了吗' },
      { icon: '📋', text: '最近身体怎么样' },
    ],
    greeting: '',

    // 语音弹窗
    showVoiceModal: false,
    voiceSegments: [] as VoiceSegment[],
    isRecording: false,
    recordingDuration: 0,
  },

  _voiceManager: null as any,
  _voiceInited: false,
  _recordTimer: null as any,
  _stopFallbackTimer: null as any,
  _currentSegText: '',

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

      const suggestions = (res.medication_suggestions || []).map((s: any) => ({
        ...s,
        _expanded: false,
        _times: 1,
        _medType: 'long_term',
        _totalDays: '',
        _interval: 1,   // ★ 默认每天
        _intervalStr: '',  // ★ 自定义输入时的字符串
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
  //  拍照上传（隐私已在登录时确认，不再弹窗）
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
  //  用药打卡 — 乐观更新
  // ════════════════════════════════════════

  onPunchTask(e: any) {
    const taskId = e.currentTarget.dataset.id
    const tasks = this.data.pendingTasks.filter((t: any) => t.id !== taskId)
    this.setData({ pendingTasks: tasks })
    wx.showToast({ title: '打卡成功', icon: 'success' })

    medsApi.completeTask(taskId).then(() => {
      this.loadHomeData()
    }).catch(() => {
      wx.showToast({ title: '打卡失败，请重试', icon: 'none' })
      this.loadHomeData()
    })
  },

  // ════════════════════════════════════════
  //  药物建议
  // ════════════════════════════════════════

  onToggleSuggestion(e: any) {
    const id = e.currentTarget.dataset.id
    const idx = this.data.medSuggestions.findIndex((s: any) => s.id === id)
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

  // ★ 频率选择
  onSugInterval(e: any) {
    const { id, interval } = e.currentTarget.dataset
    const idx = this.data.medSuggestions.findIndex((s: any) => s.id === Number(id))
    if (idx < 0) return
    const n = Number(interval)
    this.setData({
      [`medSuggestions[${idx}]._interval`]: n,
      [`medSuggestions[${idx}]._intervalStr`]: '',   // 清空自定义输入
    })
  },

  onSugIntervalInput(e: any) {
    const id = e.currentTarget.dataset.id
    const idx = this.data.medSuggestions.findIndex((s: any) => s.id === Number(id))
    if (idx < 0) return
    const raw = e.detail.value    // ★ 允许清空
    const num = Number(raw)
    this.setData({
      [`medSuggestions[${idx}]._intervalStr`]: raw,
      [`medSuggestions[${idx}]._interval`]: (num >= 1) ? num : 4,  // 保持 >=4 让面板显示
    })
  },

  onSugIntervalCustom(e: any) {
    const id = e.currentTarget.dataset.id
    const idx = this.data.medSuggestions.findIndex((s: any) => s.id === Number(id))
    if (idx < 0) return
    this.setData({
      [`medSuggestions[${idx}]._interval`]: 4,
      [`medSuggestions[${idx}]._intervalStr`]: '',   // ★ 空白让用户输入
    })
  },

  onSugDays(e: any) {
    const id = e.currentTarget.dataset.id
    const idx = this.data.medSuggestions.findIndex((s: any) => s.id === Number(id))
    if (idx < 0) return
    this.setData({ [`medSuggestions[${idx}]._totalDays`]: e.detail.value })
  },

  async onConfirmSuggestion(e: any) {
    const id = e.currentTarget.dataset.id
    const sug = this.data.medSuggestions.find((s: any) => s.id === Number(id))
    if (!sug) return

    const totalDays = Number(sug._totalDays) || 7

    try {
      // ★ 自定义频率时从字符串读取
      let intervalDays = sug._interval || 1
      if (sug._interval >= 4 && sug._intervalStr) {
        intervalDays = Math.max(1, Number(sug._intervalStr) || 1)
      }

      await medsApi.confirmSuggestion(Number(id), {
        times_per_day: sug._times,
        med_type: sug._medType,
        total_days: (sug._medType === 'course' || sug._medType === 'temporary') ? totalDays : null,
        dosage: sug.dosage,
        interval_days: intervalDays,
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
    const remaining = this.data.medSuggestions.filter((s: any) => s.id !== Number(id))
    this.setData({ medSuggestions: remaining })

    try {
      await medsApi.dismissSuggestion(Number(id))
      wx.showToast({ title: `已忽略「${sug?.name || '药物'}」`, icon: 'none' })
    } catch (err: any) {
      wx.showToast({ title: err.message || '操作失败', icon: 'none' })
      this.loadHomeData()
    }
  },

  // ════════════════════════════════════════
  //  AI 提示词 / 最近动态
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

  onActivityTap(e: any) {
    const id = e.currentTarget.dataset.id
    if (!id || String(id).startsWith('temp_')) return
    wx.navigateTo({ url: `/pages/record-detail/record-detail?id=${id}` })
  },

  // ════════════════════════════════════════
  //  语音弹窗 — 多段录音，按住说话
  // ════════════════════════════════════════

  onVoiceAdd() {
    this.setData({
      showVoiceModal: true,
      voiceSegments: [],
      isRecording: false,
      recordingDuration: 0,
    })
    this._currentSegText = ''
  },

  hideVoiceModal() {
    if (this.data.isRecording) {
      this._voiceManager?.stop?.()
    }
    this.setData({ showVoiceModal: false })
  },

  noop() {},

  onRemoveSegment(e: any) {
    const index = e.currentTarget.dataset.index
    const segs = [...this.data.voiceSegments]
    segs.splice(index, 1)
    this.setData({ voiceSegments: segs })
  },

  // ── 按住说话 / 松开结束 ──
  onRecordStart() {
    if (this.data.isRecording) return
    this._startRecording()
  },

  onRecordEnd() {
    if (!this.data.isRecording) return
    this._stopRecording()
  },

  _initVoice() {
    if (this._voiceInited) return
    const manager = plugin.getRecordRecognitionManager()

    manager.onStart = () => {
      this._currentSegText = ''
      this.setData({ isRecording: true, recordingDuration: 0 })
      this._recordTimer = setInterval(() => {
        this.setData({ recordingDuration: this.data.recordingDuration + 1 })
      }, 1000)
    }

    manager.onRecognize = (res: any) => {
      if (res.result) {
        this._currentSegText = res.result
      }
    }

    manager.onStop = (res: any) => {
      clearInterval(this._recordTimer)
      clearTimeout(this._stopFallbackTimer)

      const duration = this.data.recordingDuration
      const finalText = res.result || this._currentSegText

      this.setData({ isRecording: false })

      if (finalText && duration >= 1) {
        const seg: VoiceSegment = { duration, text: finalText }
        this.setData({
          voiceSegments: [...this.data.voiceSegments, seg],
        })
      } else if (duration < 1) {
        wx.showToast({ title: '录音太短', icon: 'none' })
      }

      this._currentSegText = ''
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

  // ★ 只检查麦克风权限（隐私已在登录时确认）
  _startRecording() {
    this._initVoice()

    wx.authorize({
      scope: 'scope.record',
      success: () => {
        this._voiceManager.start({ lang: 'zh_CN' })
        this._stopFallbackTimer = setTimeout(() => {
          if (this.data.isRecording) {
            this._stopRecording()
          }
        }, 55000)
      },
      fail: () => {
        wx.showModal({
          title: '需要录音权限',
          content: '请在设置中允许使用麦克风',
          confirmText: '去设置',
          success: (r) => { if (r.confirm) wx.openSetting() },
        })
      },
    })
  },

  _stopRecording() {
    clearTimeout(this._stopFallbackTimer)
    this._voiceManager?.stop?.()
  },

  // ── 提交：拼接 → 关窗 → 后台处理 ──
  async onSubmitVoice() {
    const segs = this.data.voiceSegments
    if (segs.length === 0 || this.data.isRecording) {
      wx.showToast({ title: '请先录音', icon: 'none' })
      return
    }

    const fullText = segs.map((s: VoiceSegment) => s.text).join('，')

    const tempId = `temp_${Date.now()}`
    const processingItem = {
      id: tempId,
      category: 'other',
      title: fullText.slice(0, 20) + (fullText.length > 20 ? '…' : ''),
      date: `${new Date().getMonth() + 1}/${new Date().getDate()}`,
      ai_status: 'processing',
      _voiceText: fullText,
    }

    this.setData({
      showVoiceModal: false,
      voiceSegments: [],
      recentActivity: [processingItem, ...this.data.recentActivity].slice(0, 5),
    })

    try {
      const res: any = await medsApi.voiceAdd(fullText)

      // ★ 显示各类型结果的 icon
      const typeIcons: Record<string, string> = {
        medication: '💊', food: '🍽️', vitals: '❤️', symptom: '📝',
        insurance: '🛡️', memo: '📋',
      }
      const items = res.items || []
      if (items.length > 0) {
        const labels = items.map((i: any) => `${typeIcons[i.type] || '✅'}${i.summary || ''}`).join(' ')
        wx.showToast({ title: labels.slice(0, 40) || '✅ 记录成功', icon: 'none', duration: 2500 })
      }

      this.loadHomeData()
    } catch (err: any) {
      const activity = this.data.recentActivity.map((item: any) => {
        if (item.id === tempId) {
          return { ...item, ai_status: 'failed' }
        }
        return item
      })
      this.setData({ recentActivity: activity })
      wx.showToast({ title: err.message || '记录失败', icon: 'none' })
    }
  },

  onRetryVoice(e: any) {
    const text = e.currentTarget.dataset.text
    if (text) {
      this.setData({ showVoiceModal: true, voiceSegments: [] })
    }
  },
})

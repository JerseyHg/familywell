/**
 * pages/home/home.ts — 首页
 * ═══════════════════════════════════════
 * ★ 审核整改：不强制登录，左上角显示"请登录"/用户名
 * ★ 语音改造：去掉WechatSI文字转换，直接录音→上传COS→后端LLM分析
 * ★ 打卡乐观更新
 * ★ Fix: 移除语音上传/分析弹窗，增加录音按钮防抖
 */

import { homeApi, medsApi, profileApi } from '../../services/api'
import { batchUpload, pollBatchAIStatus, uploadAudioToCOS } from '../../services/upload'

interface VoiceSegment {
  duration: number
  tempFilePath: string   // ★ 改为存储音频文件路径，不再存 text
}

Page({
  data: {
    isLoggedIn: false,
    avatarText: '👤',
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

  _recorder: null as WechatMiniprogram.RecorderManager | null,
  _recordTimer: null as any,
  _stopFallbackTimer: null as any,
  _recordStartTime: 0,
  _recordTouchTime: 0,       // ★ 防抖：记录触摸开始时间
  _recordDebouncing: false,   // ★ 防抖锁

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
    // ★ 审核整改：不再强制跳转登录页，只更新登录状态
    const token = wx.getStorageSync('token')
    const user = wx.getStorageSync('user')
    const isLoggedIn = !!token

    this.setData({
      isLoggedIn,
      avatarText: user?.nickname ? user.nickname.slice(0, 1) : '👤',
    })

    if (isLoggedIn) {
      this.loadHomeData()
    }

    const tabBar = this.getTabBar?.()
    if (tabBar) tabBar.setData({ active: 0 })
  },

  // ════════════════════════════════════════
  //  数据加载
  // ════════════════════════════════════════

  async loadHomeData() {
    try {
      const res: any = await homeApi.getData()
      const profile = res.profile || {}
      const tags: string[] = []
      if (profile.blood_type) tags.push(`${profile.blood_type}型`)
      if (profile.medical_history?.length) tags.push(...profile.medical_history.slice(0, 2))

      this.setData({
        profile: {
          nickname: profile.nickname || profile.real_name || '用户',
          age: profile.age,
          tags,
        },
        pendingTasks: (res.pending_tasks || []).map((t: any) => ({
          ...t,
          completing: false,
        })),
        aiTip: res.ai_tip || '',
        recentActivity: (res.recent_activity || []).slice(0, 5),
        alertCount: res.alert_count || 0,
        medSuggestions: res.medication_suggestions || [],
      })
    } catch (err) {
      console.error('loadHomeData error:', err)
    }
  },

  // ════════════════════════════════════════
  //  导航 & 操作
  // ════════════════════════════════════════

  _requireLogin(): boolean {
    if (!this.data.isLoggedIn) {
      wx.showModal({
        title: '需要登录',
        content: '请先登录后再使用此功能',
        confirmText: '去登录',
        cancelText: '取消',
        success: (res) => {
          if (res.confirm) wx.navigateTo({ url: '/pages/login/login' })
        },
      })
      return false
    }
    return true
  },

  onPhotoAdd() {
    if (!this._requireLogin()) return

    batchUpload({ maxCount: 9 })
      .then(({ recordIds }) => {
        pollBatchAIStatus(recordIds, () => this.loadHomeData())
      })
      .catch(() => {})
  },

  onPromptTap(e: any) {
    const text = e.currentTarget.dataset.text
    const app = getApp()
    app.globalData = app.globalData || {}
    app.globalData.chatInitQuestion = text
    wx.switchTab({ url: '/pages/chat/chat' })
  },

  onActivityTap(e: any) {
    const item = e.currentTarget.dataset.item
    if (item?.id && !String(item.id).startsWith('temp_')) {
      wx.navigateTo({ url: `/pages/record-detail/record-detail?id=${item.id}` })
    }
  },

  goLogin() {
    wx.navigateTo({ url: '/pages/login/login' })
  },

  // ════════════════════════════════════════
  //  打卡 — 乐观更新
  // ════════════════════════════════════════

  async onCompleteTask(e: any) {
    const taskId = e.currentTarget.dataset.id
    const idx = this.data.pendingTasks.findIndex((t: any) => t.id === taskId)
    if (idx === -1) return

    this.setData({ [`pendingTasks[${idx}].completing`]: true })

    try {
      await medsApi.completeTask(taskId)
      const tasks = this.data.pendingTasks.filter((_: any, i: number) => i !== idx)
      this.setData({ pendingTasks: tasks })
      wx.showToast({ title: '✅ 已打卡', icon: 'none' })
    } catch {
      this.setData({ [`pendingTasks[${idx}].completing`]: false })
      wx.showToast({ title: '打卡失败', icon: 'none' })
    }
  },

  // ════════════════════════════════════════
  //  药物建议确认/忽略
  // ════════════════════════════════════════

  onConfirmSuggestion(e: any) {
    const item = e.currentTarget.dataset.item
    if (!item?.id) return
    wx.navigateTo({ url: `/pages/confirm-med/confirm-med?id=${item.id}&name=${encodeURIComponent(item.name || '')}` })
  },

  async onDismissSuggestion(e: any) {
    const item = e.currentTarget.dataset.item
    if (!item?.id) return
    try {
      await medsApi.dismissSuggestion(item.id)
      const sug = this.data.medSuggestions.filter((s: any) => s.id !== item.id)
      this.setData({ medSuggestions: sug })
      wx.showToast({ title: '已忽略', icon: 'none' })
    } catch {
      wx.showToast({ title: '操作失败', icon: 'none' })
    }
  },

  // ════════════════════════════════════════
  //  ★ 语音弹窗 — 直接录音→上传COS→后端LLM分析
  // ════════════════════════════════════════

  onVoiceAdd() {
    if (!this._requireLogin()) return

    this.setData({
      showVoiceModal: true,
      voiceSegments: [],
      isRecording: false,
      recordingDuration: 0,
    })
  },

  hideVoiceModal() {
    if (this.data.isRecording) {
      this._recorder?.stop()
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

  // ── 按住说话 / 松开结束（★ 增加防抖：至少按住 300ms 才开始录音）──
  onRecordStart() {
    if (this.data.isRecording || this._recordDebouncing) return
    this._recordTouchTime = Date.now()
    this._recordDebouncing = true

    // ★ 延迟 300ms 再真正开始录音，避免误触
    setTimeout(() => {
      this._recordDebouncing = false
      // 如果 300ms 内已经松手了，不启动录音
      if (this._recordTouchTime === 0) return
      this._startRecording()
    }, 300)
  },

  onRecordEnd() {
    const holdTime = Date.now() - this._recordTouchTime
    this._recordTouchTime = 0

    // ★ 如果按住不到 300ms，取消防抖中的录音
    if (holdTime < 300) {
      this._recordDebouncing = false
      return
    }

    if (!this.data.isRecording) return
    this._stopRecording()
  },

  /** ★ 初始化 RecorderManager（替代 WechatSI 插件） */
  _initRecorder() {
    if (this._recorder) return

    const recorder = wx.getRecorderManager()

    recorder.onStart(() => {
      this._recordStartTime = Date.now()
      this.setData({ isRecording: true, recordingDuration: 0 })
      this._recordTimer = setInterval(() => {
        this.setData({ recordingDuration: this.data.recordingDuration + 1 })
      }, 1000)
    })

    recorder.onStop((res) => {
      clearInterval(this._recordTimer)
      clearTimeout(this._stopFallbackTimer)

      const duration = Math.round((Date.now() - this._recordStartTime) / 1000)
      this.setData({ isRecording: false })

      if (duration >= 1 && res.tempFilePath) {
        const seg: VoiceSegment = {
          duration,
          tempFilePath: res.tempFilePath,
        }
        this.setData({
          voiceSegments: [...this.data.voiceSegments, seg],
        })
      } else if (duration < 1) {
        wx.showToast({ title: '录音太短', icon: 'none' })
      }
    })

    recorder.onError((err: any) => {
      console.error('[Voice] recorder error:', err)
      clearInterval(this._recordTimer)
      clearTimeout(this._stopFallbackTimer)
      this.setData({ isRecording: false })
      wx.showToast({ title: '录音失败，请重试', icon: 'none' })
    })

    this._recorder = recorder
  },

  /** ★ 开始录音：使用 RecorderManager */
  _startRecording() {
    this._initRecorder()

    wx.authorize({
      scope: 'scope.record',
      success: () => {
        this._recorder!.start({
          format: 'mp3',
          sampleRate: 16000,
          numberOfChannels: 1,
          encodeBitRate: 48000,
          duration: 60000,  // 最长 60 秒
        })

        // 55 秒自动停止
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
    this._recorder?.stop()
  },

  // ── ★ 提交：上传音频到COS → 后端LLM分析（无弹窗） ──
  async onSubmitVoice() {
    const segs = this.data.voiceSegments
    if (segs.length === 0 || this.data.isRecording) {
      wx.showToast({ title: '请先录音', icon: 'none' })
      return
    }

    const tempId = `temp_${Date.now()}`
    const now = new Date()
    const processingItem = {
      id: tempId,
      category: 'other',
      title: '语音记录处理中...',
      date: `${now.getMonth() + 1}/${now.getDate()}`,
      ai_status: 'processing',
    }

    this.setData({
      showVoiceModal: false,
      voiceSegments: [],
      recentActivity: [processingItem, ...this.data.recentActivity].slice(0, 5),
    })

    // ★ 不再显示 wx.showLoading 弹窗，后台静默处理
    try {
      // 逐个上传音频文件到 COS
      const audioKeys: string[] = []
      for (let i = 0; i < segs.length; i++) {
        const seg = segs[i]
        const result = await uploadAudioToCOS(seg.tempFilePath)
        audioKeys.push(result.fileKey)
      }

      // 调用后端语音分析接口
      const res: any = await medsApi.voiceAddAudio(audioKeys)

      // 显示结果
      const typeIcons: Record<string, string> = {
        medication: '💊', food: '🍽️', vitals: '❤️', symptom: '📝',
        insurance: '🛡️', memo: '📋',
      }
      const items = res.items || []
      if (items.length > 0) {
        const labels = items.map((i: any) => `${typeIcons[i.type] || '✅'}${i.summary || ''}`).join(' ')
        wx.showToast({ title: labels.slice(0, 40) || '✅ 记录成功', icon: 'none', duration: 2500 })
      } else {
        wx.showToast({ title: '✅ 记录成功', icon: 'none' })
      }

      this.loadHomeData()
    } catch (err: any) {
      // ★ 不再 wx.hideLoading
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
    this.setData({ showVoiceModal: true, voiceSegments: [] })
  },
})

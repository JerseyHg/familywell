/**
 * pages/home/home.ts — 首页
 * ═══════════════════════════════════════
 * ★ 审核整改：不强制登录，左上角显示"请登录"/用户名
 * ★ 语音改造：去掉WechatSI文字转换，直接录音→上传COS→后端LLM分析
 * ★ 打卡乐观更新
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
      'profile.nickname': user?.nickname || '',
    })

    this.getTabBar()?.setData({ active: 0 })

    if (isLoggedIn) {
      this.checkOnboarding()
      this.loadHomeData()
    }
  },

  onPullDownRefresh() {
    if (this.data.isLoggedIn) {
      this.loadHomeData().then(() => wx.stopPullDownRefresh())
    } else {
      wx.stopPullDownRefresh()
    }
  },

  // ════════════════════════════════════════
  //  登录相关
  // ════════════════════════════════════════

  /** ★ 点击左上角登录区域 */
  onLoginTap() {
    if (this.data.isLoggedIn) {
      // 已登录 → 跳转设置页（或者不做任何事）
      wx.switchTab({ url: '/pages/settings/settings' })
    } else {
      // 未登录 → 跳转登录页
      wx.navigateTo({ url: '/pages/login/login' })
    }
  },

  /** ★ 需要登录时的统一检查 */
  _requireLogin(): boolean {
    if (!this.data.isLoggedIn) {
      wx.showModal({
        title: '需要登录',
        content: '请先登录后再使用此功能',
        confirmText: '去登录',
        cancelText: '取消',
        success: (res) => {
          if (res.confirm) {
            wx.navigateTo({ url: '/pages/login/login' })
          }
        },
      })
      return false
    }
    return true
  },

  // ════════════════════════════════════════
  //  数据加载
  // ════════════════════════════════════════

  async checkOnboarding() {
    try {
      const profile: any = await profileApi.get()
      if (!profile.onboarding_completed) {
        wx.redirectTo({ url: '/pages/onboarding/onboarding' })
      }
    } catch {}
  },

  async loadHomeData() {
    try {
      const [homeData, profileData]: any[] = await Promise.all([
        homeApi.getData(),
        profileApi.get(),
      ])

      const profile = {
        nickname: profileData.real_name || profileData.nickname || wx.getStorageSync('user')?.nickname || '',
        age: profileData.age,
        tags: [] as string[],
      }
      if (profileData.blood_type) profile.tags.push(`${profileData.blood_type}型血`)
      if (profileData.allergies?.length) profile.tags.push(`过敏: ${profileData.allergies.join('、')}`)

      this.setData({
        profile,
        avatarText: profile.nickname ? profile.nickname.slice(0, 1) : '👤',
        pendingTasks: homeData.pending_tasks || [],
        aiTip: homeData.ai_tip || '',
        recentActivity: (homeData.recent_activity || []).map((r: any) => ({
          id: r.id,
          category: r.category,
          title: r.title || '未命名',
          date: r.record_date ? r.record_date.slice(5).replace('-', '/') : '',
          ai_status: r.ai_status,
        })),
        alertCount: homeData.alert_count || 0,
        medSuggestions: homeData.med_suggestions || [],
      })
    } catch (err: any) {
      console.error('loadHomeData error:', err)
    }
  },

  // ════════════════════════════════════════
  //  拍照上传
  // ════════════════════════════════════════

  async onUpload() {
    if (!this._requireLogin()) return

    try {
      const result = await batchUpload({ maxCount: 9 })
      if (result.recordIds.length > 0) {
        pollBatchAIStatus(result.recordIds, () => this.loadHomeData())
      }
    } catch {}
  },

  // ════════════════════════════════════════
  //  用药打卡
  // ════════════════════════════════════════

  async onToggleTask(e: any) {
    const taskId = e.currentTarget.dataset.id
    const completed = e.currentTarget.dataset.completed

    if (completed) return

    // 乐观更新
    const tasks = this.data.pendingTasks.map((t: any) => {
      if (t.id === taskId) return { ...t, completed: true }
      return t
    })
    this.setData({ pendingTasks: tasks })

    try {
      await medsApi.completeTask(taskId)
      wx.showToast({ title: '✅ 已打卡', icon: 'none' })
    } catch (err: any) {
      this.loadHomeData()
      wx.showToast({ title: err.message || '打卡失败', icon: 'none' })
    }
  },

  // ════════════════════════════════════════
  //  药物建议确认/忽略
  // ════════════════════════════════════════

  async onConfirmSuggestion(e: any) {
    const id = e.currentTarget.dataset.id
    const sug = this.data.medSuggestions.find((s: any) => s.id === Number(id))
    if (!sug) return

    const intervalDays = sug.interval_days || (sug.med_type === 'every_other_day' ? 2 : 1)
    const totalDays = sug.total_days || (sug.med_type === 'long_term' ? null : 7)

    try {
      await medsApi.confirmSuggestion(Number(id), {
        times_per_day: sug.times_per_day || 1,
        med_type: sug.med_type || 'long_term',
        total_days: totalDays,
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
    if (!this._requireLogin()) return
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
  //  ★ 改造：使用 RecorderManager，不转文字
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

  // ── 按住说话 / 松开结束 ──
  onRecordStart() {
    if (this.data.isRecording) return
    this._startRecording()
  },

  onRecordEnd() {
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

  // ── ★ 提交：上传音频到COS → 后端LLM分析 ──
  async onSubmitVoice() {
    const segs = this.data.voiceSegments
    if (segs.length === 0 || this.data.isRecording) {
      wx.showToast({ title: '请先录音', icon: 'none' })
      return
    }

    const tempId = `temp_${Date.now()}`
    const processingItem = {
      id: tempId,
      category: 'other',
      title: '语音记录处理中...',
      date: `${new Date().getMonth() + 1}/${new Date().getDate()}`,
      ai_status: 'processing',
    }

    this.setData({
      showVoiceModal: false,
      voiceSegments: [],
      recentActivity: [processingItem, ...this.data.recentActivity].slice(0, 5),
    })

    wx.showLoading({ title: '上传语音中...', mask: true })

    try {
      // ★ 逐个上传音频文件到 COS
      const audioKeys: string[] = []
      for (let i = 0; i < segs.length; i++) {
        const seg = segs[i]
        const result = await uploadAudioToCOS(seg.tempFilePath)
        audioKeys.push(result.fileKey)
      }

      wx.hideLoading()
      wx.showLoading({ title: 'AI 分析中...', mask: true })

      // ★ 调用后端语音分析接口
      const res: any = await medsApi.voiceAddAudio(audioKeys)

      wx.hideLoading()

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
      wx.hideLoading()
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

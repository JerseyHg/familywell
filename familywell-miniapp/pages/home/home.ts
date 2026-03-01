/**
 * pages/home/home.ts — 首页（完整版）
 * ══════════════════════════════════════════════════════════
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
  //  ★ 属性赋值注册回调 + 追加模式
  // ════════════════════════════════════════

  noop() {},

  _ensureVoiceManager() {
    if (this._voiceInited) return

    console.log('[Voice] initializing manager...')
    const manager = plugin.getRecordRecognitionManager()
    const page = this

    // ★ 属性赋值（不是方法调用！）
    manager.onStart = function(res: any) {
      console.log('[Voice] ✅ onStart fired', res)
    }

    // ★ 追加模式：实时结果拼到 _baseText 后面
    manager.onRecognize = function(res: any) {
      console.log('[Voice] onRecognize:', res.result)
      if (res.result) {
        page.setData({ voiceText: page._baseText + res.result })
      }
    }

    // ★ 追加模式：最终结果确认后更新 _baseText
    manager.onStop = function(res: any) {
      console.log('[Voice] onStop, result:', res.result)

      if (page._stopFallbackTimer) {
        clearTimeout(page._stopFallbackTimer)
        page._stopFallbackTimer = null
      }
      if (page._recordTimer) {
        clearInterval(page._recordTimer)
        page._recordTimer = null
      }

      page.setData({ isRecording: false })

      if (res.result) {
        const finalText = page._baseText + res.result
        page.setData({ voiceText: finalText })
        // ★ 更新基础文本，加分隔符，为下一次追加做准备
        page._baseText = finalText + '，'
      } else if (res.retcode !== 0) {
        console.error('[Voice] recognition failed:', res)
        wx.showToast({ title: '语音识别失败，请重试', icon: 'none' })
      }
    }

    manager.onError = function(res: any) {
      console.error('[Voice] onError:', res.msg)
      if (page._stopFallbackTimer) {
        clearTimeout(page._stopFallbackTimer)
        page._stopFallbackTimer = null
      }
      if (page._recordTimer) {
        clearInterval(page._recordTimer)
        page._recordTimer = null
      }
      page.setData({ isRecording: false })
      wx.showToast({ title: '录音出错，请重试', icon: 'none' })
    }

    this._voiceManager = manager
    this._voiceInited = true
    console.log('[Voice] manager initialized ✅')
  },

  onVoiceAdd() {
    this.setData({
      showVoiceModal: true,
      voiceText: '',
      isRecording: false,
      recordingDuration: 0,
    })
    // ★ 打开弹窗时重置 _baseText
    this._baseText = ''
    this._ensureVoiceManager()
  },

  hideVoiceModal() {
    if (this.data.isRecording) {
      this._doStopRecording()
    }
    this.setData({ showVoiceModal: false, isRecording: false })
    this._clearAllTimers()
  },

  onVoiceTextInput(e: any) {
    this.setData({ voiceText: e.detail.value })
    // ★ 手动编辑也更新 _baseText
    this._baseText = e.detail.value
  },

  // ── 点击切换录音 ──
  onToggleRecord() {
    if (this.data.isRecording) {
      this._doStopRecording()
    } else {
      this._doStartRecording()
    }
  },

  _doStartRecording() {
    console.log('[Voice] === START FLOW ===')

    const doPrivacy = (): Promise<void> => new Promise((resolve) => {
      if (typeof wx.requirePrivacyAuthorize === 'function') {
        wx.requirePrivacyAuthorize({
          success: () => { console.log('[Voice] 1/3 privacy ✅'); resolve() },
          fail: () => { console.log('[Voice] 1/3 privacy skipped'); resolve() },
        })
      } else { resolve() }
    })

    const doAuthorize = (): Promise<boolean> => new Promise((resolve) => {
      wx.authorize({
        scope: 'scope.record',
        success: () => { console.log('[Voice] 2/3 authorize ✅'); resolve(true) },
        fail: () => {
          wx.showModal({
            title: '需要录音权限',
            content: '请在设置中允许使用麦克风',
            confirmText: '去设置',
            success: (r) => { if (r.confirm) wx.openSetting() },
          })
          resolve(false)
        },
      })
    })

    doPrivacy().then(() => doAuthorize()).then((granted) => {
      if (!granted) return

      console.log('[Voice] 3/3 starting plugin...')
      this._ensureVoiceManager()

      // ★ 追加模式：不清空 voiceText，保留之前录的内容
      this.setData({
        isRecording: true,
        recordingDuration: 0,
      })

      this._voiceManager.start({
        lang: 'zh_CN',
        duration: 60000,
      })
      console.log('[Voice] start() called')

      let seconds = 0
      this._recordTimer = setInterval(() => {
        seconds++
        this.setData({ recordingDuration: seconds })
        if (seconds >= 60) this._doStopRecording()
      }, 1000)
    })
  },

  _doStopRecording() {
    console.log('[Voice] stopping...')
    if (!this.data.isRecording) return

    if (this._recordTimer) {
      clearInterval(this._recordTimer)
      this._recordTimer = null
    }

    if (this._voiceManager) {
      this._voiceManager.stop()
      console.log('[Voice] stop() called')
    }

    this.setData({ isRecording: false })

    this._stopFallbackTimer = setTimeout(() => {
      console.warn('[Voice] onStop not fired within 3s')
      if (!this.data.voiceText) {
        wx.showToast({ title: '未识别到语音，请重试', icon: 'none' })
      }
      this._stopFallbackTimer = null
    }, 3000)
  },

  _clearAllTimers() {
    if (this._recordTimer) { clearInterval(this._recordTimer); this._recordTimer = null }
    if (this._stopFallbackTimer) { clearTimeout(this._stopFallbackTimer); this._stopFallbackTimer = null }
  },

  // ── 提交语音/文字内容 ──
  async onSubmitVoice() {
    const text = this.data.voiceText.trim()
    if (!text) {
      wx.showToast({ title: '请先说话或输入内容', icon: 'none' })
      return
    }

    if (this.data.isRecording) {
      this._doStopRecording()
      await new Promise(resolve => setTimeout(resolve, 800))
    }

    if (this.data.voiceSubmitting) return
    this.setData({ voiceSubmitting: true })

    this.setData({ showVoiceModal: false, voiceText: '' })
    this._baseText = ''

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

    try {
      const res: any = await medsApi.voiceAdd(text)

      // ★ 后端现在返回 items 数组
      const items = res.items || []
      const icons: Record<string, string> = {
        medication: '💊', food: '🍽️', vitals: '❤️', symptom: '📝',
      }

      if (items.length > 1) {
        const labels = items.map((i: any) => `${icons[i.type] || '✅'}${i.summary}`).join(' ')
        wx.showToast({ title: labels.slice(0, 40), icon: 'none', duration: 2500 })
      } else if (items.length === 1) {
        const item = items[0]
        wx.showToast({
          title: `${icons[item.type] || '✅'} ${item.summary || '记录成功'}`,
          icon: 'none', duration: 2000,
        })
      } else {
        wx.showToast({ title: '✅ 记录成功', icon: 'none', duration: 2000 })
      }

      // ★ 立即更新乐观条目状态（不等 loadHomeData）
      const updated = this.data.recentActivity.map((item: any) =>
        item.id === optimistic.id
          ? { ...item, ai_status: 'completed' }
          : item
      )
      this.setData({ recentActivity: updated })

      // 再从后端刷新完整数据
      await this.loadHomeData()
    } catch (e) {
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

  onRetryVoice(e: any) {
    const text = e.currentTarget.dataset.text
    if (!text) return
    this.setData({ voiceText: text })
    this.onSubmitVoice()
  },
})

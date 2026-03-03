/**
 * pages/chat/chat.ts — AI 健康助手
 * ═══════════════════════════════════════
 * ★ 审核整改：未登录时提示登录
 * ★ Fix 1: safe-top 在 wxml 中
 * ★ Fix 2: 完整图表模板在 wxml 中
 * ★ Fix 3: 流式输出 — chunk 模式正常逐字 + success 兜底也模拟逐字
 * ★ Fix 4: 平滑滚动
 * ★ Fix 5: 语音输入录音 + 防抖
 */
import { chatApi } from '../../services/api'
import { uploadAudioToCOS } from '../../services/upload'

interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
  isVoice?: boolean
  charts?: any[]
}

const PLACEHOLDERS = [
  '今天感觉怎么样？随时聊聊~',
  '有什么健康问题想了解的吗？',
  '我在这里，有什么需要帮忙的~',
  '想聊聊最近的身体状况吗？',
  '药吃了吗？有什么想问的尽管说~',
  '最近睡得好吗？跟我聊聊吧~',
  '有什么不舒服的地方吗？',
  '记录一下今天的健康状况吧~',
  '需要查看最近的健康数据吗？',
  '今天过得怎么样？我来帮你看看~',
]

Page({
  data: {
    // ★ 登录状态
    isLoggedIn: false,

    messages: [] as Message[],
    inputText: '',
    typing: false,
    sessionId: '',
    scrollToView: '',
    placeholder: '今天感觉怎么样？随时聊聊~',

    // ★ 语音/文字模式切换
    inputMode: 'voice' as 'voice' | 'text',
    isRecording: false,
    recordingDuration: 0,

    homePrompts: [
      { icon: '🍽️', text: '过去7天饮食情况' },
      { icon: '💊', text: '这周药吃齐了吗' },
      { icon: '💉', text: '血压最近趋势怎样' },
      { icon: '📋', text: '最近身体怎么样' },
      { icon: '📈', text: 'PSA 变化趋势' },
      { icon: '🛡️', text: '保险什么时候到期' },
      { icon: '⚠️', text: '有什么需要注意的' },
      { icon: '🏥', text: '下次该做什么检查' },
    ],

    followupPrompts: [
      { icon: '📈', text: 'PSA 变化趋势' },
      { icon: '🏥', text: '下次该做什么检查' },
      { icon: '🛡️', text: '保险什么时候到期' },
      { icon: '💊', text: '用药依从性怎么样' },
      { icon: '🍽️', text: '最近营养均衡吗' },
    ],
  },

  _streamText: '',
  _streamMsgIdx: -1,
  _streamTask: null as any,
  _throttleTimer: null as any,
  _typingTimer: null as any,

  // ★ 语音录音相关
  _recorder: null as WechatMiniprogram.RecorderManager | null,
  _recordTimer: null as any,
  _stopFallbackTimer: null as any,
  _recordStartTime: 0,
  _recordTouchTime: 0,       // ★ 防抖：记录触摸开始时间
  _recordDebouncing: false,   // ★ 防抖锁

  onShow() {
    this.getTabBar()?.setData({ active: 2 })

    // ★ 登录检查
    const token = wx.getStorageSync('token')
    this.setData({ isLoggedIn: !!token })

    const idx = Math.floor(Math.random() * PLACEHOLDERS.length)
    this.setData({ placeholder: PLACEHOLDERS[idx] })

    const app = getApp()
    const initQ = app.globalData?.chatInitQuestion
    if (initQ) {
      app.globalData.chatInitQuestion = ''
      setTimeout(() => this.sendMessage(initQ), 200)
    }
  },

  onHide() {
    this._streamTask?.abort?.()
    this._clearTimers()
    // 停止录音
    if (this.data.isRecording) {
      this._recorder?.stop()
      clearInterval(this._recordTimer)
      this.setData({ isRecording: false })
    }
  },

  // ★ 登录守卫
  _requireLogin(): boolean {
    if (!this.data.isLoggedIn) {
      wx.showModal({
        title: '需要登录',
        content: '请先登录后再使用AI助手',
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

  _clearTimers() {
    if (this._throttleTimer) { clearTimeout(this._throttleTimer); this._throttleTimer = null }
    if (this._typingTimer) { clearTimeout(this._typingTimer); this._typingTimer = null }
  },

  onInputChange(e: any) {
    this.setData({ inputText: e.detail.value })
  },

  onInputConfirm() {
    this.sendMessage(this.data.inputText)
  },

  onPromptTap(e: any) {
    this.sendMessage(e.currentTarget.dataset.text)
  },

  // ══════════════════════════════
  //  ★ 语音/文字模式切换
  // ══════════════════════════════

  onToggleInputMode() {
    const mode = this.data.inputMode === 'voice' ? 'text' : 'voice'
    this.setData({ inputMode: mode })
  },

  // ══════════════════════════════
  //  ★ 语音录音（chat 页按住说话→松开发送）
  // ══════════════════════════════

  /** 初始化录音管理器 */
  _initChatRecorder() {
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
      this.setData({ isRecording: false, recordingDuration: 0 })

      if (duration >= 1 && res.tempFilePath) {
        // ★ 直接上传并发送语音消息
        this._sendVoiceMessage(res.tempFilePath)
      } else if (duration < 1) {
        wx.showToast({ title: '录音太短', icon: 'none' })
      }
    })

    recorder.onError((err: any) => {
      console.error('[Chat Voice] recorder error:', err)
      clearInterval(this._recordTimer)
      clearTimeout(this._stopFallbackTimer)
      this.setData({ isRecording: false, recordingDuration: 0 })
      wx.showToast({ title: '录音失败，请重试', icon: 'none' })
    })

    this._recorder = recorder
  },

  /** ★ 按住开始录音（防抖：至少 300ms）*/
  onVoiceRecordStart() {
    if (this.data.isRecording || this.data.typing || this._recordDebouncing) return
    if (!this._requireLogin()) return

    this._recordTouchTime = Date.now()
    this._recordDebouncing = true

    // ★ 延迟 300ms 再真正开始录音，避免误触
    setTimeout(() => {
      this._recordDebouncing = false
      if (this._recordTouchTime === 0) return
      this._startChatRecording()
    }, 300)
  },

  /** ★ 松开结束录音 */
  onVoiceRecordEnd() {
    const holdTime = Date.now() - this._recordTouchTime
    this._recordTouchTime = 0

    // ★ 如果按住不到 300ms，取消防抖中的录音
    if (holdTime < 300) {
      this._recordDebouncing = false
      return
    }

    if (!this.data.isRecording) return
    clearTimeout(this._stopFallbackTimer)
    this._recorder?.stop()
  },

  _startChatRecording() {
    this._initChatRecorder()

    wx.authorize({
      scope: 'scope.record',
      success: () => {
        this._recorder!.start({
          format: 'mp3',
          sampleRate: 16000,
          numberOfChannels: 1,
          encodeBitRate: 48000,
          duration: 60000,
        })

        // 55 秒自动停止
        this._stopFallbackTimer = setTimeout(() => {
          if (this.data.isRecording) {
            this._recorder?.stop()
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

  /** ★ 上传语音 → 后端语音对话 */
  async _sendVoiceMessage(tempFilePath: string) {
    // 在消息列表中显示用户的语音消息
    const userMsg: Message = {
      id: `msg_${Date.now()}`,
      role: 'user',
      text: '语音提问',
      isVoice: true,
    }
    const messages = [...this.data.messages, userMsg]

    const aiMsg: Message = { id: `ai_${Date.now()}`, role: 'assistant', text: '', charts: [] }
    messages.push(aiMsg)
    const aiIdx = messages.length - 1

    this._streamText = ''
    this._streamMsgIdx = aiIdx
    this._clearTimers()

    this.setData({
      messages,
      typing: true,
      scrollToView: `msg-${aiIdx}`,
    })

    try {
      // 上传音频到 COS
      const { fileKey } = await uploadAudioToCOS(tempFilePath)

      // ★ 使用语音流式对话接口
      this._streamTask = chatApi.streamVoice(
        {
          audio_keys: [fileKey],
          session_id: this.data.sessionId || undefined,
          include_family: false,
        },
        {
          onCharts: (charts: any[]) => {
            const processed = this._processCharts(charts)
            this.setData({ [`messages[${aiIdx}].charts`]: processed })
            this._scrollToBottom()
          },

          onText: (delta: string) => {
            this._streamText += delta
            this._throttledUpdateText()
          },

          onDone: (sessionId: string) => {
            this._clearTimers()
            if (this._streamText) {
              this.setData({
                [`messages[${aiIdx}].text`]: this._streamText,
                typing: false,
                sessionId: sessionId || this.data.sessionId,
              })
            } else {
              this.setData({ typing: false })
            }
            this._scrollToBottom()
          },

          onError: (err: any) => {
            this._clearTimers()
            console.error('Voice stream error:', err)
            const errText = this._streamText || '抱歉，语音识别出错了，请重试'
            this.setData({
              [`messages[${aiIdx}].text`]: errText,
              typing: false,
            })
          },

          onFallback: (fullText: string, sessionId: string) => {
            this._clearTimers()
            this._simulateTyping(fullText, aiIdx, sessionId)
          },
        },
      )
    } catch (err) {
      console.error('Voice upload error:', err)
      this.setData({
        [`messages[${aiIdx}].text`]: '语音上传失败，请重试',
        typing: false,
      })
    }
  },

  // ══════════════════════════════
  //  核心：流式发送（文字）
  // ══════════════════════════════

  sendMessage(text: string) {
    if (!text.trim() || this.data.typing) return

    // ★ 发消息前检查登录
    if (!this._requireLogin()) return

    const question = text.trim()

    const userMsg: Message = { id: `msg_${Date.now()}`, role: 'user', text: question }
    const messages = [...this.data.messages, userMsg]

    const aiMsg: Message = { id: `ai_${Date.now()}`, role: 'assistant', text: '', charts: [] }
    messages.push(aiMsg)
    const aiIdx = messages.length - 1

    this._streamText = ''
    this._streamMsgIdx = aiIdx
    this._clearTimers()

    this.setData({
      messages,
      inputText: '',
      typing: true,
      scrollToView: `msg-${aiIdx}`,
    })

    this._streamTask = chatApi.stream(
      {
        question,
        session_id: this.data.sessionId || undefined,
        include_family: false,
      },
      {
        onCharts: (charts) => {
          const processed = this._processCharts(charts)
          this.setData({ [`messages[${aiIdx}].charts`]: processed })
          this._scrollToBottom()
        },

        onText: (delta) => {
          this._streamText += delta
          this._throttledUpdateText()
        },

        onDone: (sessionId) => {
          this._clearTimers()

          if (this._streamText) {
            this.setData({
              [`messages[${aiIdx}].text`]: this._streamText,
              typing: false,
              sessionId: sessionId || this.data.sessionId,
            })
          } else {
            this.setData({ typing: false })
          }
          this._scrollToBottom()
        },

        onError: (err) => {
          this._clearTimers()
          console.error('Stream error:', err)

          const errText = this._streamText || '抱歉，请求出错了，请稍后再试'
          this.setData({
            [`messages[${aiIdx}].text`]: errText,
            typing: false,
          })
        },

        onFallback: (fullText, sessionId) => {
          this._clearTimers()
          this._simulateTyping(fullText, aiIdx, sessionId)
        },
      },
    )
  },

  // ── 模拟逐字输出（success 兜底） ──

  _simulateTyping(fullText: string, aiIdx: number, sessionId?: string) {
    let i = 0
    const step = () => {
      if (i >= fullText.length) {
        this.setData({
          [`messages[${aiIdx}].text`]: fullText,
          typing: false,
          sessionId: sessionId || this.data.sessionId,
        })
        this._scrollToBottom()
        return
      }

      const chunk = Math.min(3, fullText.length - i)
      i += chunk
      this.setData({ [`messages[${aiIdx}].text`]: fullText.slice(0, i) })

      this._typingTimer = setTimeout(step, 30)
    }
    step()
  },

  // ── 节流更新 ──

  _throttledUpdateText() {
    if (this._throttleTimer) return
    this._throttleTimer = setTimeout(() => {
      this._throttleTimer = null
      const idx = this._streamMsgIdx
      if (idx >= 0 && this._streamText) {
        this.setData({ [`messages[${idx}].text`]: this._streamText })
        this._scrollToBottom()
      }
    }, 80)
  },

  // ── 滚动 ──

  _scrollToBottom() {
    setTimeout(() => {
      this.setData({ scrollToView: 'scroll-bottom' })
    }, 50)
  },

  // ── 处理图表数据 ──

  _processCharts(charts: any[]) {
    return charts.map((chart: any) => {
      if (chart.type === 'pie' && Array.isArray(chart.data)) {
        const total = chart.data.reduce((s: number, d: any) => s + (d.value || 0), 0)
        chart.data = chart.data.map((d: any) => ({
          ...d,
          pct: total > 0 ? Math.round((d.value / total) * 100) : 0,
        }))
      }
      return chart
    })
  },

  // ── 新对话 ──

  onNewChat() {
    this._streamTask?.abort?.()
    this._clearTimers()
    this.setData({
      messages: [],
      sessionId: '',
      typing: false,
      inputText: '',
    })
  },
})

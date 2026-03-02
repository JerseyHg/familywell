/**
 * pages/chat/chat.ts — AI 健康助手
 * ═══════════════════════════════════════
 * ★ 改造：文字输入改为语音输入（主模式），保留文字输入（辅模式）
 * ★ 语音流程：录音 → 上传COS → 后端LLM分析 → 流式返回
 * ★ 保留：流式输出、图表渲染、追问提示词
 */
import { chatApi } from '../../services/api'
import { uploadAudioToCOS } from '../../services/upload'

interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
  charts?: any[]
  isVoice?: boolean   // ★ 标记是否为语音消息
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
    messages: [] as Message[],
    inputText: '',
    typing: false,
    sessionId: '',
    scrollToView: '',
    placeholder: '今天感觉怎么样？随时聊聊~',

    // ★ 输入模式
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
  _recorder: null as WechatMiniprogram.RecorderManager | null,
  _recordTimer: null as any,
  _stopFallbackTimer: null as any,
  _recordStartTime: 0,

  onShow() {
    this.getTabBar()?.setData({ active: 2 })

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
    if (this.data.isRecording) {
      this._recorder?.stop()
    }
  },

  _clearTimers() {
    if (this._throttleTimer) { clearTimeout(this._throttleTimer); this._throttleTimer = null }
    if (this._typingTimer) { clearTimeout(this._typingTimer); this._typingTimer = null }
  },

  // ════════════════════════════════════════
  //  输入模式切换
  // ════════════════════════════════════════

  onToggleInputMode() {
    this.setData({
      inputMode: this.data.inputMode === 'voice' ? 'text' : 'voice',
    })
  },

  // ════════════════════════════════════════
  //  文字输入（辅助模式）
  // ════════════════════════════════════════

  onInputChange(e: any) {
    this.setData({ inputText: e.detail.value })
  },

  onInputConfirm() {
    this.sendMessage(this.data.inputText)
  },

  onPromptTap(e: any) {
    this.sendMessage(e.currentTarget.dataset.text)
  },

  // ════════════════════════════════════════
  //  ★ 语音输入（主模式）
  // ════════════════════════════════════════

  /** 初始化 RecorderManager */
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
        // ★ 录音完成，上传并发送
        this._handleVoiceMessage(res.tempFilePath, duration)
      } else if (duration < 1) {
        wx.showToast({ title: '录音太短', icon: 'none' })
      }
    })

    recorder.onError((err: any) => {
      console.error('[Chat Voice] recorder error:', err)
      clearInterval(this._recordTimer)
      clearTimeout(this._stopFallbackTimer)
      this.setData({ isRecording: false })
      wx.showToast({ title: '录音失败，请重试', icon: 'none' })
    })

    this._recorder = recorder
  },

  /** 按住开始录音 */
  onVoiceRecordStart() {
    if (this.data.isRecording || this.data.typing) return

    this._initRecorder()

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

  /** 松开结束录音 */
  onVoiceRecordEnd() {
    if (!this.data.isRecording) return
    clearTimeout(this._stopFallbackTimer)
    this._recorder?.stop()
  },

  /** ★ 处理语音消息：上传COS → 调用语音聊天接口 */
  async _handleVoiceMessage(tempFilePath: string, duration: number) {
    if (this.data.typing) return

    // 添加用户语音消息气泡
    const userMsg: Message = {
      id: `msg_${Date.now()}`,
      role: 'user',
      text: `🎙️ 语音提问 (${duration}秒)`,
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
      // ★ Step 1: 上传音频到 COS
      const uploadResult = await uploadAudioToCOS(tempFilePath)

      // ★ Step 2: 调用语音聊天接口（流式）
      this._streamTask = chatApi.streamVoice(
        {
          audio_keys: [uploadResult.fileKey],
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
                sessionId,
              })
            } else {
              this.setData({ typing: false, sessionId })
            }
            this._scrollToBottom()
          },

          onFallbackComplete: (fullText, charts, sessionId) => {
            if (charts.length > 0) {
              const processed = this._processCharts(charts)
              this.setData({ [`messages[${aiIdx}].charts`]: processed })
            }
            this._simulateTyping(fullText, aiIdx, sessionId)
          },

          onError: (err) => {
            console.error('[Chat Voice] stream error:', err)
            this.setData({
              [`messages[${aiIdx}].text`]: '抱歉，语音识别遇到问题，请重试',
              typing: false,
            })
          },
        }
      )
    } catch (err: any) {
      console.error('[Chat Voice] upload/send error:', err)
      this.setData({
        [`messages[${aiIdx}].text`]: '语音上传失败，请重试',
        typing: false,
      })
    }
  },

  // ════════════════════════════════════════
  //  核心：文字流式发送（保持不变）
  // ════════════════════════════════════════

  sendMessage(text: string) {
    if (!text.trim() || this.data.typing) return

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
              sessionId,
            })
          } else {
            this.setData({ typing: false, sessionId })
          }
          this._scrollToBottom()
        },

        onFallbackComplete: (fullText, charts, sessionId) => {
          if (charts.length > 0) {
            const processed = this._processCharts(charts)
            this.setData({ [`messages[${aiIdx}].charts`]: processed })
          }
          this._simulateTyping(fullText, aiIdx, sessionId)
        },

        onError: (err) => {
          console.error('[Chat] stream error:', err)
          this.setData({
            [`messages[${aiIdx}].text`]: '抱歉，AI 暂时无法回答，请稍后重试',
            typing: false,
          })
        },
      }
    )
  },

  // ════════════════════════════════════════
  //  辅助方法
  // ════════════════════════════════════════

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

  _simulateTyping(fullText: string, aiIdx: number, sessionId: string) {
    let i = 0
    const step = () => {
      if (i >= fullText.length) {
        this.setData({
          [`messages[${aiIdx}].text`]: fullText,
          typing: false,
          sessionId,
        })
        this._scrollToBottom()
        return
      }

      const chunkLen = Math.min(3 + Math.floor(Math.random() * 5), fullText.length - i)
      i += chunkLen
      this.setData({ [`messages[${aiIdx}].text`]: fullText.slice(0, i) })
      this._scrollToBottom()
      this._typingTimer = setTimeout(step, 30 + Math.random() * 40)
    }
    step()
  },

  _scrollToBottom() {
    this.setData({ scrollToView: 'scroll-bottom' })
  },

  _processCharts(charts: any[]): any[] {
    return charts.map((chart: any) => {
      if (chart.type === 'donut' && chart.data) {
        const total = chart.data.reduce((s: number, d: any) => s + (d.value || 0), 0)
        if (total > 0) {
          chart.data = chart.data.map((d: any) => ({
            ...d,
            pct: Math.round((d.value / total) * 100),
          }))
        }
      }
      return chart
    })
  },

  onNewChat() {
    this.setData({
      messages: [],
      sessionId: '',
      typing: false,
      inputText: '',
    })
    this._streamText = ''
    this._streamTask?.abort?.()
    this._clearTimers()
  },
})

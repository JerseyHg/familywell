import { chatApi } from '../../services/api'

interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
  charts?: any[]
}

Page({
  data: {
    messages: [] as Message[],
    inputText: '',
    typing: false,
    sessionId: '',
    scrollToView: '',

    homePrompts: [
      { icon: '🍽️', text: '过去7天饮食情况' },
      { icon: '💉', text: '血压最近趋势怎样' },
      { icon: '💊', text: '这周药吃齐了吗' },
      { icon: '📋', text: '最近身体怎么样' },
    ],
    followupPrompts: [
      { icon: '📈', text: 'PSA 变化趋势' },
      { icon: '🏥', text: '下次该做什么检查' },
      { icon: '🛡️', text: '保险什么时候到期' },
    ],
  },

  // 流式文本累积（不放 data 里，避免 setData 序列化开销）
  _streamText: '',
  _streamMsgIdx: -1,
  _streamTask: null as any,
  // 节流：每 80ms 才 setData 一次（小程序 setData 有性能开销）
  _throttleTimer: null as any,

  onShow() {
    this.getTabBar()?.setData({ active: 1 })

    const app = getApp()
    const initQ = app.globalData?.chatInitQuestion
    if (initQ) {
      app.globalData.chatInitQuestion = ''
      setTimeout(() => this.sendMessage(initQ), 200)
    }
  },

  onHide() {
    // 离开页面时取消进行中的流式请求
    this._streamTask?.abort?.()
  },

  // ── Input handlers ──

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
  //  核心：流式发送
  // ════════════════════════════════════════

  sendMessage(text: string) {
    if (!text.trim() || this.data.typing) return

    const question = text.trim()

    // 1. 添加用户消息
    const userMsg: Message = { id: `msg_${Date.now()}`, role: 'user', text: question }
    const messages = [...this.data.messages, userMsg]

    // 2. 预创建空的 AI 消息占位（后续流式填充）
    const aiMsg: Message = { id: `ai_${Date.now()}`, role: 'assistant', text: '', charts: [] }
    messages.push(aiMsg)
    const aiIdx = messages.length - 1

    this._streamText = ''
    this._streamMsgIdx = aiIdx

    this.setData({
      messages,
      inputText: '',
      typing: true,
      scrollToView: `msg-${aiIdx}`,
    })

    // 3. 发起流式请求
    this._streamTask = chatApi.stream(
      {
        question,
        session_id: this.data.sessionId || undefined,
        include_family: false,
      },
      {
        // ── 图表先到（<100ms），立即渲染 ──
        onCharts: (charts) => {
          this.setData({
            [`messages[${aiIdx}].charts`]: charts,
          })
          this._scrollToBottom()
        },

        // ── 文字逐块到达，节流更新 UI ──
        onText: (delta) => {
          this._streamText += delta
          this._throttledUpdateText()
        },

        // ── 结束信号 ──
        onDone: (sessionId) => {
          // 清理节流定时器，做最后一次 flush
          if (this._throttleTimer) {
            clearTimeout(this._throttleTimer)
            this._throttleTimer = null
          }

          this.setData({
            [`messages[${aiIdx}].text`]: this._streamText,
            sessionId: sessionId || this.data.sessionId,
            typing: false,
          })
          this._scrollToBottom()
          this._streamTask = null
        },

        // ── 错误处理：降级到同步模式 ──
        onError: (err) => {
          console.error('Stream failed, falling back to sync:', err)
          this._streamTask = null
          this._fallbackSync(question, aiIdx)
        },
      },
    )
  },

  /**
   * 节流更新文字：每 80ms 最多 setData 一次。
   * 避免每个 token（10-50ms 间隔）都触发 setData。
   */
  _throttledUpdateText() {
    if (this._throttleTimer) return
    this._throttleTimer = setTimeout(() => {
      this._throttleTimer = null
      const idx = this._streamMsgIdx
      if (idx >= 0) {
        this.setData({
          [`messages[${idx}].text`]: this._streamText,
        })
        this._scrollToBottom()
      }
    }, 80)
  },

  /**
   * 降级到同步模式：如果流式失败（老版本小程序不支持 enableChunkedTransfer 等）
   */
  async _fallbackSync(question: string, aiIdx: number) {
    try {
      const res: any = await chatApi.send({
        question,
        session_id: this.data.sessionId || undefined,
        include_family: false,
      })

      this.setData({
        [`messages[${aiIdx}].text`]: res.answer || '',
        [`messages[${aiIdx}].charts`]: res.charts || [],
        sessionId: res.session_id || this.data.sessionId,
        typing: false,
      })
    } catch (err2) {
      this.setData({
        [`messages[${aiIdx}].text`]: '抱歉，AI 助手暂时无法回答，请稍后再试。',
        typing: false,
      })
    }
  },

  _scrollToBottom() {
    const idx = this.data.messages.length - 1
    this.setData({ scrollToView: `msg-${idx}` })
  },

  // ── New conversation ──
  onNewChat() {
    this._streamTask?.abort?.()
    this.setData({
      messages: [],
      sessionId: '',
      typing: false,
    })
  },
})

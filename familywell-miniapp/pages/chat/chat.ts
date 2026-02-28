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

    // ✅ 优化：更丰富的欢迎页模板问题（2x4 网格）
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

    // ✅ 优化：更丰富的追问提示词（根据对话上下文动态更新）
    followupPrompts: [
      { icon: '📈', text: 'PSA 变化趋势' },
      { icon: '🏥', text: '下次该做什么检查' },
      { icon: '🛡️', text: '保险什么时候到期' },
      { icon: '💊', text: '用药依从性怎么样' },
      { icon: '🍽️', text: '最近营养均衡吗' },
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

          // ✅ 根据对话内容动态更新追问提示词
          this._updateFollowupPrompts(question)
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
   * ✅ 根据用户最后一个问题，动态切换追问提示词
   */
  _updateFollowupPrompts(lastQuestion: string) {
    const q = lastQuestion.toLowerCase()

    // 根据上一个话题推荐相关追问
    let prompts = []

    if (q.includes('药') || q.includes('服药') || q.includes('用药')) {
      prompts = [
        { icon: '📅', text: '本周哪天漏服了' },
        { icon: '💊', text: '药还剩多少需要补' },
        { icon: '📋', text: '最近身体怎么样' },
      ]
    } else if (q.includes('饮食') || q.includes('营养') || q.includes('吃')) {
      prompts = [
        { icon: '🥩', text: '蛋白质摄入够吗' },
        { icon: '📊', text: '和上周对比怎么样' },
        { icon: '💊', text: '这周药吃齐了吗' },
      ]
    } else if (q.includes('血压')) {
      prompts = [
        { icon: '📈', text: '近一个月血压趋势' },
        { icon: '💊', text: '降压药吃齐了吗' },
        { icon: '🏥', text: '需要去医院复查吗' },
      ]
    } else if (q.includes('psa') || q.includes('前列腺')) {
      prompts = [
        { icon: '📈', text: 'PSA 和上次比变化大吗' },
        { icon: '🏥', text: '下次该做什么检查' },
        { icon: '📋', text: '最近身体怎么样' },
      ]
    } else if (q.includes('保险') || q.includes('保单')) {
      prompts = [
        { icon: '💰', text: '保费什么时候交' },
        { icon: '📋', text: '保险都覆盖什么' },
        { icon: '📋', text: '最近身体怎么样' },
      ]
    } else {
      // 默认追问
      prompts = [
        { icon: '📈', text: 'PSA 变化趋势' },
        { icon: '💊', text: '这周药吃齐了吗' },
        { icon: '🍽️', text: '最近营养均衡吗' },
        { icon: '🛡️', text: '保险什么时候到期' },
        { icon: '⚠️', text: '有什么需要注意的' },
      ]
    }

    this.setData({ followupPrompts: prompts })
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

/**
 * pages/chat/chat.ts — AI 健康助手
 * ═══════════════════════════════════════
 * ★ Fix 1: safe-top 在 wxml 中
 * ★ Fix 2: 完整图表模板在 wxml 中
 * ★ Fix 3: 流式输出 — chunk 模式正常逐字 + success 兜底也模拟逐字
 * ★ Fix 4: 平滑滚动
 */
import { chatApi } from '../../services/api'

interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
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
    messages: [] as Message[],
    inputText: '',
    typing: false,
    sessionId: '',
    scrollToView: '',
    placeholder: '今天感觉怎么样？随时聊聊~',

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

  // ════════════════════════════════════════
  //  核心：流式发送
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
          // ★ Fix 1: 为饼图数据补充 pct 百分比
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

          // ★ 如果 chunk 模式正常工作了，直接设置最终文本
          if (this._streamText) {
            this.setData({
              [`messages[${aiIdx}].text`]: this._streamText,
              sessionId: sessionId || this.data.sessionId,
              typing: false,
            })
            this._scrollToBottom()
          } else {
            // 没收到任何 text（不应该发生），标记完成
            this.setData({
              sessionId: sessionId || this.data.sessionId,
              typing: false,
            })
          }

          this._streamTask = null
          this._updateFollowupPrompts(question)
        },

        // ★ Fix 3: success 兜底 — chunk 没工作时，拿到完整数据做模拟打字
        onFallbackComplete: (fullText, charts, sessionId) => {
          this._clearTimers()

          // 先设置 charts
          if (charts && charts.length) {
            const processed = this._processCharts(charts)
            this.setData({ [`messages[${aiIdx}].charts`]: processed })
          }

          // 模拟逐字输出
          this._simulateTyping(aiIdx, fullText, () => {
            this.setData({
              sessionId: sessionId || this.data.sessionId,
              typing: false,
            })
            this._scrollToBottom()
            this._updateFollowupPrompts(question)
          })
        },

        onError: (err) => {
          console.error('Stream failed, falling back to sync:', err)
          this._streamTask = null
          this._fallbackSync(question, aiIdx)
        },
      },
    )
  },

  _updateFollowupPrompts(lastQuestion: string) {
    const q = lastQuestion.toLowerCase()

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

  _throttledUpdateText() {
    if (this._throttleTimer) return
    this._throttleTimer = setTimeout(() => {
      this._throttleTimer = null
      const idx = this._streamMsgIdx
      if (idx >= 0) {
        this.setData({ [`messages[${idx}].text`]: this._streamText })
        this._scrollToBottom()
      }
    }, 60)
  },

  /**
   * ★ 模拟打字效果（用于 success 兜底和 sync fallback）
   */
  _simulateTyping(aiIdx: number, fullText: string, onComplete: () => void) {
    let pos = 0
    const chunkSize = 4
    const interval = 25

    const tick = () => {
      if (pos >= fullText.length) {
        this.setData({ [`messages[${aiIdx}].text`]: fullText })
        this._scrollToBottom()
        onComplete()
        return
      }

      pos = Math.min(pos + chunkSize, fullText.length)
      this.setData({ [`messages[${aiIdx}].text`]: fullText.slice(0, pos) })
      this._scrollToBottom()
      this._typingTimer = setTimeout(tick, interval)
    }

    tick()
  },

  /**
   * 完全同步 fallback（stream 失败时）
   */
  async _fallbackSync(question: string, aiIdx: number) {
    try {
      const res: any = await chatApi.send({
        question,
        session_id: this.data.sessionId || undefined,
        include_family: false,
      })

      if (res.charts && res.charts.length) {
        const processed = this._processCharts(res.charts)
        this.setData({ [`messages[${aiIdx}].charts`]: processed })
      }

      const fullText = res.answer || ''
      this._simulateTyping(aiIdx, fullText, () => {
        this.setData({
          sessionId: res.session_id || this.data.sessionId,
          typing: false,
        })
        this._updateFollowupPrompts(question)
      })
    } catch (err2) {
      this.setData({
        [`messages[${aiIdx}].text`]: '抱歉，AI 助手暂时无法回答，请稍后再试。',
        typing: false,
      })
    }
  },

  /**
   * ★ Fix 1: 为图表数据做预处理
   * — pie 图：计算每段的 pct + 累计百分比 + gradient 字符串
   */
  _processCharts(charts: any[]): any[] {
    return charts.map((chart: any) => {
      if (chart.type === 'pie' && chart.data && chart.data.length) {
        const total = chart.data.reduce((s: number, d: any) => s + (d.value || 0), 0)
        if (total === 0) return chart

        let cumPct = 0
        const data = chart.data.map((d: any) => {
          const pct = Math.round((d.value / total) * 100)
          const startPct = cumPct
          cumPct += pct
          return { ...d, pct, startPct, endPct: cumPct }
        })
        // 修正尾差
        if (data.length > 0) data[data.length - 1].endPct = 100

        // 生成 conic-gradient 字符串
        const gradientParts = data.map((d: any) => `${d.color} ${d.startPct}% ${d.endPct}%`)
        const gradient = `conic-gradient(${gradientParts.join(', ')})`

        return { ...chart, data, _gradient: gradient }
      }
      return chart
    })
  },

  _scrollToBottom() {
    this.setData({ scrollToView: '' })
    setTimeout(() => {
      this.setData({ scrollToView: 'scroll-bottom' })
    }, 50)
  },

  onNewChat() {
    this._streamTask?.abort?.()
    this._clearTimers()
    this.setData({
      messages: [],
      sessionId: '',
      typing: false,
    })
    const idx = Math.floor(Math.random() * PLACEHOLDERS.length)
    this.setData({ placeholder: PLACEHOLDERS[idx] })
  },
})

/**
 * pages/chat/chat.ts — AI 健康助手
 * ═══════════════════════════════════════
 * ★ 审核整改：未登录时提示登录
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
    // ★ 登录状态
    isLoggedIn: false,

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
  //  核心：流式发送
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

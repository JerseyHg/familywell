/**
 * pages/chat/chat.ts — AI 健康助手
 * ═══════════════════════════════════════
 * ★ 修复：随机 placeholder 提示词（Problem 3）
 * ★ 修复：追问提示词改为 wrap 布局（Problem 5）
 */
import { chatApi } from '../../services/api'

interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
  charts?: any[]
}

// ★ 温和语气的随机 placeholder
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

    // ✅ 欢迎页模板问题（2x4 网格）
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

    // 追问提示词
    followupPrompts: [
      { icon: '📈', text: 'PSA 变化趋势' },
      { icon: '🏥', text: '下次该做什么检查' },
      { icon: '🛡️', text: '保险什么时候到期' },
      { icon: '💊', text: '用药依从性怎么样' },
      { icon: '🍽️', text: '最近营养均衡吗' },
    ],
  },

  // 流式文本累积
  _streamText: '',
  _streamMsgIdx: -1,
  _streamTask: null as any,
  _throttleTimer: null as any,

  onShow() {
    this.getTabBar()?.setData({ active: 2 })

    // ★ 每次进入页面随机选一个 placeholder
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

    // 2. 预创建空的 AI 消息占位
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
        onCharts: (charts) => {
          this.setData({ [`messages[${aiIdx}].charts`]: charts })
          this._scrollToBottom()
        },

        onText: (delta) => {
          this._streamText += delta
          this._throttledUpdateText()
        },

        onDone: (sessionId) => {
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
          this._updateFollowupPrompts(question)
        },

        onError: (err) => {
          console.error('Stream failed, falling back to sync:', err)
          this._streamTask = null
          this._fallbackSync(question, aiIdx)
        },
      },
    )
  },

  /**
   * 根据用户最后一个问题，动态切换追问提示词
   */
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
    }, 80)
  },

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

  onNewChat() {
    this._streamTask?.abort?.()
    this.setData({
      messages: [],
      sessionId: '',
      typing: false,
    })
    // 新对话也换一个 placeholder
    const idx = Math.floor(Math.random() * PLACEHOLDERS.length)
    this.setData({ placeholder: PLACEHOLDERS[idx] })
  },
})

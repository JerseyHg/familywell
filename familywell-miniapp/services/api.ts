/**
 * services/api.ts — API 请求封装
 * ─────────────────────────────────
 * ★ [Fix-4] SSE 流式请求增加 180s 超时（语音 ASR 可能需要较长时间）
 * ★ [Fix-4] _collectSSEData 增加 transcript 提取
 * ★ [Fix-5] 429 状态码：status 轮询静默失败，不弹 toast
 * ★ [Fix-4] ChatStreamCallbacks 增加 onFallback 兼容
 */

function getBaseUrl(): string {
  const accountInfo = wx.getAccountInfoSync()
  const envVersion = accountInfo.miniProgram.envVersion

  switch (envVersion) {
    case 'release':
      return 'https://api.tbowo.top/familywell/api'
    case 'trial':
      return 'https://api.tbowo.top/familywell/api'
    default:
      return 'https://api.tbowo.top/familywell/api'
  }
}

const BASE_URL = getBaseUrl()

interface RequestOptions {
  url: string
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  data?: any
  header?: Record<string, string>
  showLoading?: boolean
  silent429?: boolean  // ★ [Fix-5] 新增：429 时是否静默（不弹 toast）
}

// ★ 延迟导入 cache，避免循环依赖
function _clearCacheOnLogout() {
  try {
    const cache = require('./cache')
    cache.clearAllCache()
  } catch { /* ignore if cache module not ready */ }
}

function getToken(): string {
  return wx.getStorageSync('token') || ''
}

export function request<T = any>(options: RequestOptions): Promise<T> {
  const { url, method = 'GET', data, header = {}, showLoading = false, silent429 = false } = options

  if (showLoading) {
    wx.showLoading({ title: '加载中', mask: true })
  }

  const token = getToken()
  if (token) {
    header['Authorization'] = `Bearer ${token}`
  }
  header['Content-Type'] = header['Content-Type'] || 'application/json'

  return new Promise((resolve, reject) => {
    wx.request({
      url: `${BASE_URL}${url}`,
      method,
      data,
      header,
      success(res) {
        if (showLoading) wx.hideLoading()

        if (res.statusCode === 401) {
          wx.removeStorageSync('token')
          wx.removeStorageSync('user')
          _clearCacheOnLogout()
          wx.redirectTo({ url: '/pages/login/login' })
          reject(new Error('未登录'))
          return
        }

        if (res.statusCode === 429) {
          // ★ [Fix-5] status 轮询等场景静默处理，不弹 toast
          if (!silent429) {
            wx.showToast({ title: '操作太频繁，请稍后再试', icon: 'none' })
          }
          reject(new Error('rate_limited'))
          return
        }

        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data as T)
        } else {
          console.log('[request] error status:', res.statusCode, JSON.stringify(res.data))
          const msg = (res.data as any)?.detail || '请求失败'
          wx.showToast({ title: msg, icon: 'none' })
          reject(new Error(msg))
        }
      },
      fail(err) {
        if (showLoading) wx.hideLoading()
        console.log('[request] fail details:', JSON.stringify(err))
        wx.showToast({ title: '网络错误', icon: 'none' })
        reject(err)
      },
    })
  })
}

// ─── Auth ───
export const authApi = {
  register: (data: { username: string; password: string; nickname?: string }) =>
    request({ url: '/auth/register', method: 'POST', data }),

  login: (data: { username: string; password: string }) =>
    request({ url: '/auth/login', method: 'POST', data }),

  wxLogin: (data: { code: string; nickname?: string; avatar_url?: string }) =>
    request({ url: '/auth/wx-login', method: 'POST', data }),

  deleteAccount: () =>
    request({ url: '/auth/account', method: 'DELETE' }),

  me: () => request({ url: '/auth/me' }),
}

// ─── Home ───
export const homeApi = {
  getData: () => request({ url: '/home' }),
}

// ─── Profile ───
export const profileApi = {
  get: () => request({ url: '/profile' }),
  update: (data: any) => request({ url: '/profile', method: 'PUT', data }),
  voiceParse: (data: { step: string; text: string }) =>
    request({ url: '/profile/voice-parse', method: 'POST', data }),
}

// ─── Records ───
export const recordsApi = {
  getUploadUrl: (data: { file_name: string; content_type: string }) =>
    request({ url: '/records/upload-url', method: 'POST', data }),

  create: (data: { file_key: string; file_type: string; source: string; project_id?: number }) =>
    request({ url: '/records', method: 'POST', data }),

  // ★ [Fix-5] status 轮询使用 silent429，避免频繁弹 toast
  getStatus: (id: number) =>
    request({ url: `/records/${id}/status`, silent429: true }),

  list: (params: {
    category?: string;
    project_id?: number;
    unassigned?: boolean;
    page?: number;
    size?: number;
  } = {}) => {
    const query = Object.entries(params)
      .filter(([_, v]) => v !== undefined && v !== false)
      .map(([k, v]) => `${k}=${v}`)
      .join('&')
    return request({ url: `/records${query ? '?' + query : ''}` })
  },

  detail: (id: number) => request({ url: `/records/${id}` }),

  update: (id: number, data: any) =>
    request({ url: `/records/${id}`, method: 'PUT', data }),

  confirmPrescription: (id: number, medications: any[]) =>
    request({
      url: `/records/${id}/confirm-prescription`,
      method: 'POST',
      data: { medications },
    }),
}

// ─── Projects ───
export const projectsApi = {
  create: (data: {
    name: string;
    description?: string;
    icon?: string;
    start_date?: string;
    end_date?: string;
    template?: string;
  }) => request({ url: '/projects', method: 'POST', data }),

  list: (status?: string) => {
    const q = status ? `?status=${status}` : ''
    return request({ url: `/projects${q}` })
  },

  detail: (id: number) => request({ url: `/projects/${id}` }),

  update: (id: number, data: any) =>
    request({ url: `/projects/${id}`, method: 'PUT', data }),

  delete: (id: number) =>
    request({ url: `/projects/${id}`, method: 'DELETE' }),

  assignRecords: (projectId: number, recordIds: number[]) =>
    request({ url: `/projects/${projectId}/records`, method: 'POST', data: { record_ids: recordIds } }),

  removeRecords: (projectId: number, recordIds: number[]) =>
    request({ url: `/projects/${projectId}/records`, method: 'DELETE', data: { record_ids: recordIds } }),
}

// ─── Medications ───
export const medsApi = {
  list: (activeOnly = true) =>
    request({ url: `/medications?active_only=${activeOnly}` }),

  create: (data: any) =>
    request({ url: '/medications', method: 'POST', data }),

  update: (id: number, data: any) =>
    request({ url: `/medications/${id}`, method: 'PUT', data }),

  tasks: (startDate: string, endDate: string) =>
    request({ url: `/medications/tasks?start_date=${startDate}&end_date=${endDate}` }),

  completeTask: (taskId: number) =>
    request({ url: `/medications/tasks/${taskId}/complete`, method: 'PUT' }),

  voiceAdd: (text: string) =>
      request({ url: '/voice/add', method: 'POST', data: { text } }),

  voiceAddAudio: (audioKeys: string[]) =>
      request({ url: '/voice/add-audio', method: 'POST', data: { audio_keys: audioKeys } }),

  confirmSuggestion: (id: number, data: {
    times_per_day?: number;
    med_type?: string;
    total_days?: number | null;
    dosage?: string | null;
    interval_days?: number;
  }) =>
    request({ url: `/medications/suggestions/${id}/confirm`, method: 'POST', data }),

  dismissSuggestion: (id: number) =>
    request({ url: `/medications/suggestions/${id}/dismiss`, method: 'POST' }),
}

// ─── Stats ───
export const statsApi = {
  indicators: (type: string, period = '6m') =>
    request({ url: `/stats/indicators?type=${type}&period=${period}` }),

  nutrition: (period = '7d') =>
    request({ url: `/stats/nutrition?period=${period}` }),

  bp: (period = '30d') =>
    request({ url: `/stats/bp?period=${period}` }),

  medAdherence: (period = '7d') =>
    request({ url: `/stats/medication-adherence?period=${period}` }),
}

// ─── Families ───
export const familyApi = {
  create: (name?: string) =>
    request({ url: '/families', method: 'POST', data: { name } }),

  getMyFamily: () => request({ url: '/families/mine' }),

  mine: () => request({ url: '/families/mine' }),

  join: (inviteCode: string) =>
    request({ url: '/families/join', method: 'POST', data: { invite_code: inviteCode } }),

  overview: (familyId: number) =>
    request({ url: `/families/${familyId}/overview` }),

  members: (familyId: number) =>
    request({ url: `/families/${familyId}/members` }),

  removeMember: (familyId: number, userId: number) =>
    request({ url: `/families/${familyId}/members/${userId}`, method: 'DELETE' }),
}

// ─── Reminders ───
export const reminderApi = {
  list: () => request({ url: '/reminders' }),

  markRead: (id: number) =>
    request({ url: `/reminders/${id}/read`, method: 'PUT' }),

  markResolved: (id: number) =>
    request({ url: `/reminders/${id}/resolve`, method: 'PUT' }),

  getSettings: () => request({ url: '/reminders/settings' }),

  updateSettings: (data: any) =>
    request({ url: '/reminders/settings', method: 'PUT', data }),
}

// ─── Chat ───

interface ChatParams {
  question: string
  session_id?: string
  include_family?: boolean
}

interface ChatVoiceParams {
  audio_keys: string[]
  session_id?: string
  include_family?: boolean
}

interface ChatStreamCallbacks {
  onCharts?: (charts: any[]) => void
  onText?: (delta: string) => void
  onDone?: (sessionId: string) => void
  onError?: (err: any) => void
  onTranscript?: (text: string) => void
  // ★ [Fix-4] 完整的 fallback 回调（3 参数）
  onFallbackComplete?: (fullText: string, charts: any[], sessionId: string) => void
  // ★ [Fix-4] 兼容旧版 2 参数的 onFallback（chat.js 旧代码可能还在用）
  onFallback?: (fullText: string, sessionId: string) => void
}

export const chatApi = {
  stream(params: ChatParams, callbacks: ChatStreamCallbacks) {
    return _doStreamRequest(`${BASE_URL}/chat/stream`, params, callbacks)
  },

  streamVoice(params: ChatVoiceParams, callbacks: ChatStreamCallbacks) {
    return _doStreamRequest(`${BASE_URL}/chat/stream-voice`, params, callbacks)
  },

  send(params: ChatParams) {
    return request({
      url: '/chat',
      method: 'POST',
      data: params,
    })
  },
}

/** 通用 SSE 流式请求实现 */
function _doStreamRequest(url: string, params: any, callbacks: ChatStreamCallbacks) {
  const token = getToken()
  let fullReceived = ''
  let processedLen = 0
  let chunkedUsed = false

  const task = wx.request({
    url,
    method: 'POST',
    data: params,
    // ★ [Fix-4] 加长超时到 180 秒，给语音 ASR 留足时间
    timeout: 180000,
    header: {
      'Content-Type': 'application/json',
      Authorization: token ? `Bearer ${token}` : '',
    },
    enableChunkedTransfer: true,
    responseType: 'text',

    success(res) {
      if (chunkedUsed) return

      if (typeof res.data === 'string') {
        const collected = _collectSSEData(res.data)

        // ★ [Fix-4] 优先用 onFallbackComplete（3 参数），再兼容 onFallback（2 参数）
        if (callbacks.onFallbackComplete) {
          // ★ [Fix-6] fallback 路径也要触发 onTranscript（语音消息显示实际内容）
          if (collected.transcript && callbacks.onTranscript) {
            callbacks.onTranscript(collected.transcript)
          }
          callbacks.onFallbackComplete(
            collected.fullText,
            collected.charts,
            collected.sessionId,
          )
        } else if (callbacks.onFallback) {
          // 兼容旧版：先单独触发 transcript 和 charts
          if (collected.transcript && callbacks.onTranscript) {
            callbacks.onTranscript(collected.transcript)
          }
          if (collected.charts.length > 0 && callbacks.onCharts) {
            callbacks.onCharts(collected.charts)
          }
          callbacks.onFallback(collected.fullText, collected.sessionId)
        } else {
          _parseAllSSELines(res.data, callbacks)
        }
      }
    },

    fail(err) {
      callbacks.onError?.(err)
    },
  })

  if (task && typeof task.onChunkReceived === 'function') {
    task.onChunkReceived((resp: { data: ArrayBuffer }) => {
      try {
        chunkedUsed = true
        const chunk = _arrayBufferToString(resp.data)
        fullReceived += chunk

        const unprocessed = fullReceived.slice(processedLen)
        const lines = unprocessed.split('\n')

        for (let i = 0; i < lines.length - 1; i++) {
          const line = lines[i].trim()
          processedLen += lines[i].length + 1

          if (!line.startsWith('data:')) continue
          const jsonStr = line.slice(5).trim()
          if (!jsonStr) continue

          try {
            const evt = JSON.parse(jsonStr)
            _handleSSEEvent(evt, callbacks)
          } catch { /* incomplete JSON, skip */ }
        }
      } catch (e) {
        console.error('[Chat SSE] chunk parse error:', e)
      }
    })
  }

  return task
}

// ─── SSE 解析辅助函数 ───

function _arrayBufferToString(buf: ArrayBuffer): string {
  if (typeof TextDecoder !== 'undefined') {
    return new TextDecoder('utf-8').decode(buf)
  }
  const bytes = new Uint8Array(buf)
  let str = ''
  for (let i = 0; i < bytes.length; i++) {
    str += String.fromCharCode(bytes[i])
  }
  try {
    return decodeURIComponent(escape(str))
  } catch {
    return str
  }
}

// ★ [Fix-4] 增加 transcript 字段的提取
function _collectSSEData(fullData: string): {
  fullText: string;
  charts: any[];
  sessionId: string;
  transcript: string;
} {
  let fullText = ''
  let charts: any[] = []
  let sessionId = ''
  let transcript = ''

  const lines = fullData.split('\n')
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed.startsWith('data:')) continue
    const jsonStr = trimmed.slice(5).trim()
    if (!jsonStr) continue
    try {
      const evt = JSON.parse(jsonStr)
      if (evt.type === 'text') fullText += (evt.content || '')
      else if (evt.type === 'charts') charts = evt.charts || []
      else if (evt.type === 'done') sessionId = evt.session_id || ''
      else if (evt.type === 'transcript') transcript = evt.content || ''
    } catch { /* skip */ }
  }

  return { fullText, charts, sessionId, transcript }
}

function _parseAllSSELines(fullData: string, callbacks: ChatStreamCallbacks) {
  const lines = fullData.split('\n')
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed.startsWith('data:')) continue
    const jsonStr = trimmed.slice(5).trim()
    if (!jsonStr) continue
    try {
      const evt = JSON.parse(jsonStr)
      _handleSSEEvent(evt, callbacks)
    } catch { /* skip */ }
  }
}

function _handleSSEEvent(evt: any, callbacks: ChatStreamCallbacks) {
  switch (evt.type) {
    case 'charts':
      callbacks.onCharts?.(evt.charts || [])
      break
    case 'sources':
      break
    case 'text':
      callbacks.onText?.(evt.content || '')
      break
    case 'transcript':
      callbacks.onTranscript?.(evt.content || '')
      break
    case 'done':
      callbacks.onDone?.(evt.session_id || '')
      break
    case 'error':
      callbacks.onError?.(evt)
      break
  }
}

// ─── Search ───
export const searchApi = {
  search: (q: string, category?: string) => {
    const params = new URLSearchParams({ q })
    if (category) params.append('category', category)
    return request({ url: `/search?${params}` })
  },
}

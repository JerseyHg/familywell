/**
 * services/api.ts — API 请求封装
 * ─────────────────────────────────
 * [P1-1] recordsApi 新增 update, confirmPrescription
 * [P1-2] authApi 新增 wxLogin
 * ★ chatApi 补全 stream / send 方法
 */

const BASE_URL = 'https://tbowo.top/familywell/api'

interface RequestOptions {
  url: string
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  data?: any
  header?: Record<string, string>
  showLoading?: boolean
}

function getToken(): string {
  return wx.getStorageSync('token') || ''
}

export function request<T = any>(options: RequestOptions): Promise<T> {
  const { url, method = 'GET', data, header = {}, showLoading = false } = options

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
          wx.removeStorageSync('userInfo')
          wx.redirectTo({ url: '/pages/login/login' })
          reject(new Error('未登录'))
          return
        }

        if (res.statusCode === 429) {
          wx.showToast({ title: '操作太频繁，请稍后再试', icon: 'none' })
          reject(new Error('rate_limited'))
          return
        }

        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data as T)
        } else {
          const msg = (res.data as any)?.detail || '请求失败'
          wx.showToast({ title: msg, icon: 'none' })
          reject(new Error(msg))
        }
      },
      fail(err) {
        if (showLoading) wx.hideLoading()
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

  getStatus: (id: number) =>
    request({ url: `/records/${id}/status` }),

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
    request({ url: '/medications/voice-add', method: 'POST', data: { text } }),
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

  // settings.ts 用的是 mine()，加别名兼容
  mine: () => request({ url: '/families/mine' }),

  join: (inviteCode: string) =>
    request({ url: '/families/join', method: 'POST', data: { invite_code: inviteCode } }),

  overview: (familyId: number) =>
    request({ url: `/families/${familyId}/overview` }),

  // settings.ts 需要加载家庭成员列表
  members: (familyId: number) =>
    request({ url: `/families/${familyId}/members` }),

  // settings.ts 需要移除成员
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
// ★ 流式（SSE）+ 同步 fallback

interface ChatParams {
  question: string
  session_id?: string
  include_family?: boolean
}

interface ChatStreamCallbacks {
  onCharts?: (charts: any[]) => void
  onText?: (delta: string) => void
  onDone?: (sessionId: string) => void
  onError?: (err: any) => void
}

export const chatApi = {
  /**
   * ★ SSE 流式请求
   * 后端逐行返回 `data: {...}\n\n`，前端通过 enableChunkedTransfer 接收分块
   */
  stream(params: ChatParams, callbacks: ChatStreamCallbacks) {
    const token = getToken()
    let fullReceived = ''    // 累积收到的全部文本
    let processedLen = 0     // 已解析到的位置

    const task = wx.request({
      url: `${BASE_URL}/chat/stream`,
      method: 'POST',
      data: params,
      header: {
        'Content-Type': 'application/json',
        Authorization: token ? `Bearer ${token}` : '',
      },
      enableChunkedTransfer: true,
      responseType: 'text',

      success(res) {
        // 全部接收完后做一次完整解析（兜底）
        if (typeof res.data === 'string') {
          _parseAllSSELines(res.data, callbacks)
        }
      },

      fail(err) {
        callbacks.onError?.(err)
      },
    })

    // ★ 监听分块数据到达
    if (task && typeof task.onChunkReceived === 'function') {
      task.onChunkReceived((resp: { data: ArrayBuffer }) => {
        try {
          const chunk = _arrayBufferToString(resp.data)
          fullReceived += chunk

          // 只解析新到达的部分
          const unprocessed = fullReceived.slice(processedLen)
          const lines = unprocessed.split('\n')

          // 最后一行可能不完整，保留到下次
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
    } else {
      console.warn('[Chat] onChunkReceived not supported, will fallback on complete')
    }

    return task
  },

  /**
   * 同步模式 fallback
   */
  send(params: ChatParams) {
    return request({
      url: '/chat',
      method: 'POST',
      data: params,
    })
  },
}

// ─── SSE 解析辅助函数 ───

/** ArrayBuffer → UTF-8 字符串 */
function _arrayBufferToString(buf: ArrayBuffer): string {
  // 优先用 TextDecoder（较新的基础库支持）
  if (typeof TextDecoder !== 'undefined') {
    return new TextDecoder('utf-8').decode(buf)
  }
  // 兜底：逐字节拼接
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

/** 兜底：对完整的 SSE 文本做一次全量解析 */
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
      // sources 暂不需要回调，可扩展
      break
    case 'text':
      callbacks.onText?.(evt.content || '')
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

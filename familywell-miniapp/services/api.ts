// 开发调试（微信开发者工具勾选「不校验域名」）
// const BASE_URL = 'http://你的服务器IP:8004/api'
// 生产环境
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
}

// ─── Projects (归档项目) ───
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

  mine: () => request({ url: '/families/mine' }),

  join: (inviteCode: string) =>
    request({ url: '/families/join', method: 'POST', data: { invite_code: inviteCode } }),

  members: (familyId: number) =>
    request({ url: `/families/${familyId}/members` }),

  overview: (familyId: number) =>
    request({ url: `/families/${familyId}/overview` }),

  removeMember: (familyId: number, userId: number) =>
    request({ url: `/families/${familyId}/members/${userId}`, method: 'DELETE' }),
}

// ─── Reminders ───
export const reminderApi = {
  list: (unreadOnly = false, page = 1) =>
    request({ url: `/reminders?unread_only=${unreadOnly}&page=${page}` }),

  urgent: () => request({ url: '/reminders/urgent' }),

  markRead: (id: number) =>
    request({ url: `/reminders/${id}/read`, method: 'PUT' }),

  getSettings: () => request({ url: '/reminders/settings' }),

  updateSettings: (data: any) =>
    request({ url: '/reminders/settings', method: 'PUT', data }),
}

// ─── Chat (AI 健康助手) ───
export const chatApi = {
  // 同步模式（fallback）
  send: (data: { question: string; session_id?: string; include_family?: boolean }) =>
    request({ url: '/chat', method: 'POST', data }),

  /**
   * ★ 流式模式 — SSE 逐字推送
   *
   * 回调顺序:
   *   onCharts(charts)     ← 图表先到（<100ms）
   *   onSources(sources)   ← 引用来源
   *   onText(delta)        ← 文字逐块到达（多次调用）
   *   onDone(session_id)   ← 结束
   */
  stream: (
    data: { question: string; session_id?: string; include_family?: boolean },
    callbacks: {
      onCharts?: (charts: any[]) => void
      onSources?: (sources: any[]) => void
      onText?: (delta: string) => void
      onDone?: (sessionId: string) => void
      onError?: (err: any) => void
    },
  ) => {
    const token = getToken()
    const task = wx.request({
      url: `${BASE_URL}/chat/stream`,
      method: 'POST',
      enableChunkedTransfer: true,
      header: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      data,
      success(res) {
        // fallback: 如果 onChunkReceived 不触发，尝试从完整响应解析
        if (res.statusCode >= 200 && res.statusCode < 300) {
          const text = typeof res.data === 'string' ? res.data : JSON.stringify(res.data)
          const lines = text.split('\n')
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const payload = JSON.parse(line.slice(6))
              switch (payload.type) {
                case 'charts': callbacks.onCharts?.(payload.charts || []); break
                case 'sources': callbacks.onSources?.(payload.sources || []); break
                case 'text': callbacks.onText?.(payload.content || ''); break
                case 'done': callbacks.onDone?.(payload.session_id || ''); break
              }
            } catch (_) {}
          }
        }
      },
      fail(err) {
        callbacks.onError?.(err)
      },
    })
    let chunkedReceived = false

    // 用于处理跨 chunk 的不完整行
    let buffer = ''

    task.onChunkReceived?.((res: { data: ArrayBuffer }) => {
      chunkedReceived = true
      try {
        // ArrayBuffer → string
        const bytes = new Uint8Array(res.data)
        let text = ''
        for (let i = 0; i < bytes.length; i++) {
          text += String.fromCharCode(bytes[i])
        }
        // 处理 UTF-8 多字节（中文等）
        try {
          text = decodeURIComponent(escape(text))
        } catch (_) { /* 不是 UTF-8 就用原文 */ }

        buffer += text

        // 按 SSE 协议解析完整的 "data: {...}\n\n" 行
        const parts = buffer.split('\n\n')
        // 最后一个可能不完整，留在 buffer
        buffer = parts.pop() || ''

        for (const part of parts) {
          const lines = part.split('\n')
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            const jsonStr = line.slice(6) // 去掉 "data: "
            if (!jsonStr.trim()) continue

            try {
              const payload = JSON.parse(jsonStr)

              switch (payload.type) {
                case 'charts':
                  callbacks.onCharts?.(payload.charts || [])
                  break
                case 'sources':
                  callbacks.onSources?.(payload.sources || [])
                  break
                case 'text':
                  callbacks.onText?.(payload.content || '')
                  break
                case 'done':
                  callbacks.onDone?.(payload.session_id || '')
                  break
              }
            } catch (parseErr) {
              console.warn('SSE parse error:', parseErr, jsonStr)
            }
          }
        }
      } catch (e) {
        console.error('onChunkReceived error:', e)
      }
    })

    return task  // 返回 RequestTask，可用 task.abort() 取消
  },

  sessions: () => request({ url: '/chat/sessions' }),

  sessionMessages: (sessionId: string) =>
    request({ url: `/chat/sessions/${sessionId}` }),

  deleteSession: (sessionId: string) =>
    request({ url: `/chat/sessions/${sessionId}`, method: 'DELETE' }),
}

// ─── Search (语义搜索) ───
export const searchApi = {
  search: (q: string, topK = 10, contentType?: string) => {
    let url = `/search?q=${encodeURIComponent(q)}&top_k=${topK}`
    if (contentType) url += `&content_type=${contentType}`
    return request({ url })
  },
}

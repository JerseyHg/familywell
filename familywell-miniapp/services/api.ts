/**
 * services/api.ts — API 请求封装
 * ─────────────────────────────────
 * [P1-1] recordsApi 新增 update, confirmPrescription
 * [P1-2] authApi 新增 wxLogin
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

        // [P0-2] 处理速率限制
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

  // [P1-2] 微信登录
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

  // [P1-1] 记录详情（含 ai_raw_result 和图片URL）
  detail: (id: number) => request({ url: `/records/${id}` }),

  // [P1-1] 编辑记录
  update: (id: number, data: any) =>
    request({ url: `/records/${id}`, method: 'PUT', data }),

  // [P1-3] 确认处方药物
  confirmPrescription: (id: number, medications: any[]) =>
    request({
      url: `/records/${id}/confirm-prescription`,
      method: 'POST',
      data: { medications },
    }),
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

  getMyFamily: () => request({ url: '/families/my' }),

  join: (inviteCode: string) =>
    request({ url: '/families/join', method: 'POST', data: { invite_code: inviteCode } }),

  overview: () => request({ url: '/families/overview' }),
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
export const chatApi = {
  // SSE 流式接口在 chat 页面中直接用 wx.request 处理
}

// ─── Search ───
export const searchApi = {
  search: (q: string, category?: string) => {
    const params = new URLSearchParams({ q })
    if (category) params.append('category', category)
    return request({ url: `/search?${params}` })
  },
}

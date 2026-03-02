import { recordsApi, projectsApi } from '../../services/api'

const CATEGORY_MAP: Record<string, string> = {
  checkup: '体检',
  lab: '化验',
  prescription: '处方',
  medication_log: '服药记录',
  insurance: '保险',
  food: '饮食',
  bp_reading: '血压',
  weight: '体重',
  visit: '就诊',
  other: '其他',
}

const CATEGORIES = [
  { key: '', icon: '📋', label: '全部' },
  { key: 'checkup,lab', icon: '🏥', label: '医疗' },
  { key: 'prescription,medication_log', icon: '💊', label: '用药' },
  { key: 'food', icon: '🍽️', label: '饮食' },
  { key: 'insurance', icon: '🛡️', label: '保险' },
  { key: 'bp_reading,weight', icon: '❤️', label: '健康数据' },
]

const TEMPLATES = [
  { key: 'chemo_cycle', icon: '💉', name: '化疗周期' },
  { key: 'annual_checkup', icon: '📊', name: '年度体检' },
  { key: 'pregnancy', icon: '🤰', name: '孕期' },
  { key: 'hospitalization', icon: '🏥', name: '住院' },
  { key: 'weight_loss', icon: '⚖️', name: '减重' },
  { key: 'rehab', icon: '🦿', name: '康复' },
  { key: 'chronic', icon: '💊', name: '慢病管理' },
  { key: 'custom', icon: '📁', name: '自定义' },
]

function formatDate(d: string | null): string {
  if (!d) return ''
  return d.slice(0, 10)
}

function formatDateRange(start: string | null, end: string | null): string {
  if (!start && !end) return '无时间范围'
  const s = start ? start.slice(5, 10).replace('-', '/') : '?'
  const e = end ? end.slice(5, 10).replace('-', '/') : '至今'
  return `${s} — ${e}`
}

Page({
  data: {
    // ★ 登录状态
    isLoggedIn: false,

    viewMode: 'projects' as 'projects' | 'category',

    // 项目视图
    activeProjects: [] as any[],
    archivedProjects: [] as any[],
    unassignedRecords: [] as any[],

    // 分类视图
    categories: CATEGORIES,
    activeCat: '',
    catRecords: [] as any[],

    // 新建项目
    showCreateModal: false,
    templates: TEMPLATES,
    newProject: {
      name: '',
      description: '',
      icon: '📁',
      template: 'custom',
      start_date: '',
      end_date: '',
    },
    creating: false,
  },

  onShow() {
    const tabBar = this.getTabBar?.()
    if (tabBar) tabBar.setData({ active: 1 })

    // ★ 登录检查：未登录不调接口
    const token = wx.getStorageSync('token')
    const isLoggedIn = !!token
    this.setData({ isLoggedIn })

    if (isLoggedIn) {
      this.loadData()
    }
  },

  // ★ 登录守卫
  _requireLogin(): boolean {
    if (!this.data.isLoggedIn) {
      wx.showModal({
        title: '需要登录',
        content: '请先登录后再使用此功能',
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

  goLogin() {
    wx.navigateTo({ url: '/pages/login/login' })
  },

  // ═══════════════════════════════
  //  数据加载
  // ═══════════════════════════════

  async loadData() {
    if (this.data.viewMode === 'projects') {
      await this.loadProjects()
    } else {
      await this.loadCategoryRecords()
    }
  },

  async loadProjects() {
    try {
      const projects: any = await projectsApi.list()
      const active = (projects || [])
        .filter((p: any) => !p.is_archived)
        .map((p: any) => ({
          ...p,
          dateRange: formatDateRange(p.start_date, p.end_date),
        }))
      const archived = (projects || [])
        .filter((p: any) => p.is_archived)
        .map((p: any) => ({
          ...p,
          dateRange: formatDateRange(p.start_date, p.end_date),
        }))

      this.setData({ activeProjects: active, archivedProjects: archived })

      // 加载未归项目的记录
      try {
        const unassigned: any = await recordsApi.list({ no_project: true, size: 20 })
        this.setData({
          unassignedRecords: (unassigned.items || []).map((r: any) => ({
            ...r,
            displayDate: formatDate(r.record_date || r.created_at),
            categoryName: CATEGORY_MAP[r.category] || r.category,
          })),
        })
      } catch {}
    } catch (e) {
      console.error('Failed to load projects:', e)
    }
  },

  async loadCategoryRecords() {
    try {
      const catKey = this.data.activeCat
      const params: any = { size: 50 }
      if (catKey) params.category = catKey

      const res: any = await recordsApi.list(params)
      if (res?.items) {
        this.setData({
          catRecords: res.items.map((r: any) => ({
            ...r,
            displayDate: formatDate(r.record_date || r.created_at),
            categoryName: CATEGORY_MAP[r.category] || r.category,
          })),
        })
      }
    } catch (e) {
      console.error('Failed to load records:', e)
    }
  },

  // ─── 视图切换 ───

  switchView(e: any) {
    if (!this._requireLogin()) return
    const mode = e.currentTarget.dataset.mode
    this.setData({ viewMode: mode })
    this.loadData()
  },

  onCatTap(e: any) {
    if (!this._requireLogin()) return
    this.setData({ activeCat: e.currentTarget.dataset.key })
    this.loadCategoryRecords()
  },

  // ─── 项目交互 ───

  onProjectTap(e: any) {
    if (!this._requireLogin()) return
    const id = e.currentTarget.dataset.id
    wx.navigateTo({ url: `/pages/archive/project-detail?id=${id}` })
  },

  // ─── 新建项目 ───

  onCreateProject() {
    if (!this._requireLogin()) return

    this.setData({
      showCreateModal: true,
      newProject: {
        name: '',
        description: '',
        icon: '📁',
        template: 'custom',
        start_date: '',
        end_date: '',
      },
    })
  },

  noop() {},

  hideCreateModal() {
    this.setData({ showCreateModal: false })
  },

  onTemplateTap(e: any) {
    const { key, icon, name } = e.currentTarget.dataset
    this.setData({
      'newProject.template': key,
      'newProject.icon': icon,
      'newProject.name': key === 'custom' ? '' : name,
    })
  },

  onProjectNameInput(e: any) {
    this.setData({ 'newProject.name': e.detail.value })
  },

  onProjectDescInput(e: any) {
    this.setData({ 'newProject.description': e.detail.value })
  },

  onStartDateChange(e: any) {
    this.setData({ 'newProject.start_date': e.detail.value })
  },

  onEndDateChange(e: any) {
    this.setData({ 'newProject.end_date': e.detail.value })
  },

  async onSubmitProject() {
    const { name, description, icon, template, start_date, end_date } = this.data.newProject

    if (!name.trim()) {
      wx.showToast({ title: '请输入项目名称', icon: 'none' })
      return
    }

    this.setData({ creating: true })

    try {
      await projectsApi.create({
        name: name.trim(),
        description: description || undefined,
        icon,
        template,
        start_date: start_date || undefined,
        end_date: end_date || undefined,
      })

      wx.showToast({ title: '创建成功', icon: 'success' })
      this.setData({ showCreateModal: false, creating: false })
      this.loadProjects()
    } catch (e) {
      this.setData({ creating: false })
      console.error('Failed to create project:', e)
    }
  },

  onRecordTap(e: any) {
    if (!this._requireLogin()) return
    const id = e.currentTarget.dataset.id
    if (!id) return
    wx.navigateTo({ url: `/pages/record-detail/record-detail?id=${id}` })
  },
})

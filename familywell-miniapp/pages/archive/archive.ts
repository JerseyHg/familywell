import { recordsApi, projectsApi } from '../../services/api'

const CATEGORY_MAP: Record<string, string> = {
  checkup: '体检',
  lab: '化验',
  prescription: '处方',
  medication_log: '服药记录',   // ★ 新增
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
  { key: 'prescription,medication_log', icon: '💊', label: '用药' },   // ★ 加入 medication_log
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

    this.loadData()
  },

  async loadData() {
    if (this.data.viewMode === 'projects') {
      await this.loadProjects()
    } else {
      await this.loadCategoryRecords()
    }
  },

  // ─── 项目视图 ───

  async loadProjects() {
    try {
      const [projectRes, unassignedRes] = await Promise.all([
        projectsApi.list() as Promise<any>,
        recordsApi.list({ unassigned: true, size: 50 }) as Promise<any>,
      ])

      const projects = (projectRes.items || []).map((p: any) => ({
        ...p,
        dateRange: formatDateRange(p.start_date, p.end_date),
      }))

      this.setData({
        activeProjects: projects.filter((p: any) => p.status === 'active'),
        archivedProjects: projects.filter((p: any) => p.status === 'archived'),
        unassignedRecords: (unassignedRes.items || []).map((r: any) => ({
          ...r,
          displayDate: formatDate(r.record_date || r.created_at),
          categoryName: CATEGORY_MAP[r.category] || r.category,
        })),
      })
    } catch (e) {
      console.error('Failed to load projects:', e)
    }
  },

  // ─── 分类视图 ───

  async loadCategoryRecords() {
    try {
      const catKey = this.data.activeCat
      const params: any = { size: 50 }
      if (catKey) {
        // 合并分类：分别请求再合并
        const keys = catKey.split(',')
        let allItems: any[] = []
        for (const k of keys) {
          const res: any = await recordsApi.list({ category: k, size: 50 })
          allItems = allItems.concat(res.items || [])
        }
        allItems.sort((a: any, b: any) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        )
        this.setData({
          catRecords: allItems.map((r: any) => ({
            ...r,
            displayDate: formatDate(r.record_date || r.created_at),
            categoryName: CATEGORY_MAP[r.category] || r.category,
          })),
        })
      } else {
        const res: any = await recordsApi.list(params)
        this.setData({
          catRecords: (res.items || []).map((r: any) => ({
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
    const mode = e.currentTarget.dataset.mode
    this.setData({ viewMode: mode })
    this.loadData()
  },

  onCatTap(e: any) {
    this.setData({ activeCat: e.currentTarget.dataset.key })
    this.loadCategoryRecords()
  },

  // ─── 项目交互 ───

  onProjectTap(e: any) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({ url: `/pages/archive/project-detail?id=${id}` })
  },

  // ─── 新建项目 ───

  onCreateProject() {
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
    const id = e.currentTarget.dataset.id
    if (!id) return
    wx.navigateTo({ url: `/pages/record-detail/record-detail?id=${id}` })
  },
})

/**
 * pages/archive/project-detail.ts
 * ★ Fix 4: 移出记录后立即更新本地计数（乐观更新）
 */
import { projectsApi, recordsApi } from '../../services/api'
import { formatDate as helperFormatDate, parseLocalDate } from '../../utils/helpers'

const CATEGORY_CONFIG: { key: string; label: string; icon: string; cats: string[] }[] = [
  { key: 'medical', label: '医疗', icon: '🩺', cats: ['checkup', 'lab', 'visit', 'bp_reading'] },
  { key: 'prescription', label: '用药', icon: '💊', cats: ['prescription', 'medication_log'] },
  { key: 'nutrition', label: '饮食', icon: '🍽️', cats: ['food', 'weight'] },
  { key: 'insurance', label: '保险', icon: '🛡️', cats: ['insurance'] },
  { key: 'other', label: '其他', icon: '📄', cats: ['other'] },
]

const ICON_OPTIONS = ['📁', '💉', '📊', '🤰', '🏥', '⚖️', '🦿', '💊', '🫀', '🧪', '🩺', '🏋️']

// ★ Fix: 使用 helpers 中的时区感知日期格式化
function formatDate(d: string | null): string {
  if (!d) return ''
  return helperFormatDate(d, 'short')
}

function formatDateRange(start: string | null, end: string | null): string {
  if (!start && !end) return '无时间范围'
  const sf = start ? parseLocalDate(start) : null
  const ef = end ? parseLocalDate(end) : null
  const s = sf ? `${sf.getMonth() + 1}/${sf.getDate()}` : '?'
  const e = ef ? `${ef.getMonth() + 1}/${ef.getDate()}` : '至今'
  return `${s} — ${e}`
}

function groupByCategory(records: any[]): any[] {
  const groups = CATEGORY_CONFIG.map(cfg => ({
    key: cfg.key,
    label: cfg.label,
    icon: cfg.icon,
    records: records.filter(r => cfg.cats.includes(r.category)),
  }))
  return groups.filter(g => g.records.length > 0)
}

Page({
  data: {
    projectId: 0,
    project: {} as any,
    records: [] as any[],
    categoryGroups: [] as any[],
    loading: true,

    showEditModal: false,
    iconOptions: ICON_OPTIONS,
    editForm: {
      name: '',
      description: '',
      icon: '📁',
      start_date: '',
      end_date: '',
    },
    saving: false,
  },

  onLoad(options: any) {
    const id = parseInt(options.id)
    if (!id) {
      wx.showToast({ title: '项目不存在', icon: 'none' })
      wx.navigateBack()
      return
    }
    this.setData({ projectId: id })
    this.loadData()
  },

  async loadData() {
    this.setData({ loading: true })
    try {
      const [project, recordsRes] = await Promise.all([
        projectsApi.detail(this.data.projectId) as Promise<any>,
        recordsApi.list({ project_id: this.data.projectId, size: 50 }) as Promise<any>,
      ])

      wx.setNavigationBarTitle({ title: project.name || '项目详情' })

      const records = (recordsRes.items || []).map((r: any) => ({
        ...r,
        displayDate: formatDate(r.record_date || r.created_at),
      }))

      this.setData({
        project: {
          ...project,
          record_count: records.length,   // ★ 用实际记录数，而非 API 返回的 count
          dateRange: formatDateRange(project.start_date, project.end_date),
        },
        records,
        categoryGroups: groupByCategory(records),
        loading: false,
      })
    } catch (e) {
      console.error('Failed to load project:', e)
      this.setData({ loading: false })
    }
  },

  // ── 编辑项目 ──

  noop() {},

  onEditProject() {
    const p = this.data.project
    this.setData({
      showEditModal: true,
      editForm: {
        name: p.name || '',
        description: p.description || '',
        icon: p.icon || '📁',
        start_date: p.start_date ? p.start_date.slice(0, 10) : '',
        end_date: p.end_date ? p.end_date.slice(0, 10) : '',
      },
    })
  },

  hideEditModal() {
    this.setData({ showEditModal: false })
  },

  onEditNameInput(e: any) {
    this.setData({ 'editForm.name': e.detail.value })
  },

  onEditDescInput(e: any) {
    this.setData({ 'editForm.description': e.detail.value })
  },

  onEditIconTap(e: any) {
    this.setData({ 'editForm.icon': e.currentTarget.dataset.icon })
  },

  onEditStartDate(e: any) {
    this.setData({ 'editForm.start_date': e.detail.value })
  },

  onEditEndDate(e: any) {
    this.setData({ 'editForm.end_date': e.detail.value })
  },

  async onSubmitEdit() {
    const { name, description, icon, start_date, end_date } = this.data.editForm

    if (!name.trim()) {
      wx.showToast({ title: '名称不能为空', icon: 'none' })
      return
    }

    this.setData({ saving: true })

    try {
      await projectsApi.update(this.data.projectId, {
        name: name.trim(),
        description: description || null,
        icon,
        start_date: start_date || null,
        end_date: end_date || null,
      })

      wx.showToast({ title: '已保存', icon: 'success' })
      this.setData({ showEditModal: false, saving: false })
      this.loadData()
    } catch (e) {
      this.setData({ saving: false })
      console.error('Failed to update project:', e)
    }
  },

  // ── 结束项目 ──

  onEndProject() {
    wx.showModal({
      title: '结束项目',
      content: '标记为已结束？记录不会被删除。',
      success: async (res) => {
        if (res.confirm) {
          await projectsApi.update(this.data.projectId, { status: 'archived' })
          wx.showToast({ title: '已结束', icon: 'success' })
          this.loadData()
        }
      },
    })
  },

  // ── 删除项目 ──

  onDeleteProject() {
    wx.showModal({
      title: '删除项目',
      content: '删除后记录不会丢失，只是解除归属。',
      confirmColor: '#E85D3A',
      success: async (res) => {
        if (res.confirm) {
          await projectsApi.delete(this.data.projectId)
          wx.showToast({ title: '已删除', icon: 'success' })
          wx.navigateBack()
        }
      },
    })
  },

  // ── ★ Fix 4: 移出记录 — 乐观更新 ──

  onRemoveRecord(e: any) {
    const recordId = e.currentTarget.dataset.id
    wx.showModal({
      title: '移出项目',
      content: '将该记录从项目中移出？',
      success: async (res) => {
        if (res.confirm) {
          // ★ 立即从本地数据中移除（乐观更新）
          const newRecords = this.data.records.filter((r: any) => r.id !== recordId)
          this.setData({
            records: newRecords,
            categoryGroups: groupByCategory(newRecords),
            'project.record_count': newRecords.length,
          })

          try {
            await projectsApi.removeRecords(this.data.projectId, [recordId])
            wx.showToast({ title: '已移出', icon: 'success' })
          } catch (err) {
            console.error('Failed to remove record:', err)
            wx.showToast({ title: '移出失败', icon: 'none' })
            // 失败则重新加载
            this.loadData()
          }
        }
      },
    })
  },
})

/**
 * pages/record-detail/record-detail.ts — 记录详情页
 * ────────────────────────────────────────────────────
 * [P1-1] 查看 AI 识别结果 + 原始图片 + 编辑功能
 * [P1-3] 处方类记录支持确认药物
 *
 * 入口：从首页最近动态、归档页、项目详情页点击进入
 * URL: /pages/record-detail/record-detail?id=123
 */
import { recordsApi } from '../../services/api'
import { invalidation } from '../../services/cache'

// 分类中文映射
const CATEGORY_MAP: Record<string, string> = {
  checkup: '体检报告', lab: '化验单', prescription: '处方',
  insurance: '保险', food: '饮食', bp_reading: '血压',
  visit: '就诊', other: '其他',
}
const CATEGORY_ICON: Record<string, string> = {
  checkup: '📊', lab: '🔬', prescription: '📝',
  insurance: '🛡️', food: '🍽️', bp_reading: '❤️',
  visit: '🏥', other: '📄',
}

Page({
  data: {
    recordId: 0,
    loading: true,
    record: null as any,
    categoryName: '',
    categoryIcon: '',
    imageUrl: '',

    // AI 识别结果（结构化展示）
    indicators: [] as any[],
    medications: [] as any[],
    rawText: '',
    validationWarnings: [] as string[],

    // 编辑模式
    editing: false,
    editForm: {} as any,

    // 处方确认
    needConfirm: false,
    medConfirmList: [] as any[],
  },

  onLoad(options: any) {
    const id = parseInt(options.id)
    if (!id) {
      wx.showToast({ title: '记录不存在', icon: 'none' })
      wx.navigateBack()
      return
    }
    this.setData({ recordId: id })
    this.loadDetail(id)
  },

  async loadDetail(id: number) {
    this.setData({ loading: true })
    try {
      const record: any = await recordsApi.detail(id)
      const ai = record.ai_raw_result || {}

      this.setData({
        record,
        categoryName: CATEGORY_MAP[record.category] || record.category,
        categoryIcon: CATEGORY_ICON[record.category] || '📄',
        imageUrl: record.image_url || '',
        indicators: ai.indicators || [],
        medications: ai.medications || [],
        rawText: ai.raw_text || '',
        validationWarnings: ai._validation_warnings || [],
        needConfirm: record.ai_status === 'pending_confirmation' && record.category === 'prescription',
        loading: false,
      })

      // 构建处方确认列表
      if (this.data.needConfirm) {
        const medConfirmList = (ai.medications || []).map((m: any, i: number) => ({
          ...m,
          index: i,
          confirmed: true,  // 默认全部勾选
        }))
        this.setData({ medConfirmList })
      }

      // 设置页面标题
      wx.setNavigationBarTitle({ title: record.title || this.data.categoryName })

    } catch (e) {
      console.error('Load record detail failed:', e)
      wx.showToast({ title: '加载失败', icon: 'none' })
    }
  },

  // ── 预览原图 ──
  onPreviewImage() {
    if (!this.data.imageUrl) return
    wx.previewImage({
      current: this.data.imageUrl,
      urls: [this.data.imageUrl],
    })
  },

  // ── 编辑模式 ──
  onStartEdit() {
    const { record } = this.data
    this.setData({
      editing: true,
      editForm: {
        title: record.title || '',
        hospital: record.hospital || '',
        record_date: record.record_date || '',
        notes: record.notes || '',
      },
    })
  },

  onCancelEdit() {
    this.setData({ editing: false })
  },

  onEditInput(e: any) {
    const key = e.currentTarget.dataset.key
    this.setData({ [`editForm.${key}`]: e.detail.value })
  },

  onEditDate(e: any) {
    this.setData({ 'editForm.record_date': e.detail.value })
  },

  async onSaveEdit() {
    const { recordId, editForm } = this.data
    try {
      wx.showLoading({ title: '保存中...', mask: true })
      await recordsApi.update(recordId, editForm)
      wx.hideLoading()
      wx.showToast({ title: '已保存', icon: 'success' })
      this.setData({ editing: false })
      invalidation.onRecordChange()
      this.loadDetail(recordId)
    } catch (e) {
      wx.hideLoading()
      wx.showToast({ title: '保存失败', icon: 'none' })
    }
  },

  // ── [P1-3] 处方确认 ──
  onToggleMed(e: any) {
    const idx = e.currentTarget.dataset.index
    const list = [...this.data.medConfirmList]
    list[idx].confirmed = !list[idx].confirmed
    this.setData({ medConfirmList: list })
  },

  async onConfirmPrescription() {
    const { recordId, medConfirmList } = this.data
    const medications = medConfirmList.map((m: any) => ({
      name: m.name,
      dosage: m.dosage,
      frequency: m.frequency,
      times: m.times,
      confirmed: m.confirmed,
    }))

    try {
      wx.showLoading({ title: '确认中...', mask: true })
      const res: any = await recordsApi.confirmPrescription(recordId, medications)
      wx.hideLoading()
      wx.showToast({ title: res.message || '已确认', icon: 'success' })
      this.setData({ needConfirm: false })
      this.loadDetail(recordId)
    } catch (e) {
      wx.hideLoading()
      wx.showToast({ title: '确认失败', icon: 'none' })
    }
  },

  // ── 删除记录 ──
  onDeleteRecord() {
    wx.showModal({
      title: '删除记录',
      content: '删除后无法恢复，确定要删除这条记录吗？',
      confirmColor: '#E53935',
      success: (res) => {
        if (res.confirm) {
          this._doDelete()
        }
      },
    })
  },

  async _doDelete() {
    const { recordId } = this.data
    try {
      wx.showLoading({ title: '删除中...', mask: true })
      await recordsApi.delete(recordId)
      wx.hideLoading()
      wx.showToast({ title: '已删除', icon: 'success' })
      invalidation.onRecordChange()
      setTimeout(() => wx.navigateBack(), 500)
    } catch (e) {
      wx.hideLoading()
      wx.showToast({ title: '删除失败', icon: 'none' })
    }
  },

  // ── 跳转AI问答 ──
  onAskAI() {
    const { record } = this.data
    const question = record.title ? `帮我解读一下"${record.title}"` : '帮我解读一下最近的检查结果'
    wx.switchTab({
      url: '/pages/chat/chat',
      success: () => {
        getApp().globalData.chatInitQuestion = question
      },
    })
  },
})

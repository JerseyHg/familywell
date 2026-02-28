import { profileApi, medsApi } from '../../services/api'

const DEFAULT_TIMES: Record<number, string[]> = {
  1: ['08:00'],
  2: ['08:00', '20:00'],
  3: ['08:00', '12:00', '20:00'],
}

const MED_TYPE_OPTIONS = [
  { val: 'long_term', icon: '🔄', label: '长期' },
  { val: 'course', icon: '📅', label: '疗程' },
  { val: 'temporary', icon: '🤒', label: '临时' },
]

function calcEndDate(startDate: string, days: number): string {
  const d = new Date(startDate)
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

function todayStr(): string {
  return new Date().toISOString().slice(0, 10)
}

Page({
  data: {
    // 模式：onboard=注册引导（4步走完） edit=设置页编辑（单步保存返回）
    mode: 'onboard' as 'onboard' | 'edit',
    step: 1,
    saving: false,

    // Step 1: 基本信息
    form: {
      real_name: '',
      gender: '',
      birthday: '',
      blood_type: '',
      // Step 2
      medical_history: [] as string[],
      allergies: [] as string[],
      // Step 4
      emergency_contact_name: '',
      emergency_contact_phone: '',
    },
    bloodTypes: ['A', 'B', 'AB', 'O', '不确定'],

    // Step 2
    diseaseOptions: ['高血压', '糖尿病', '心脏病', '哮喘', '甲状腺', '高血脂', '痛风', '肿瘤', '肾病', '肝病'],
    allergyOptions: ['青霉素', '头孢', '磺胺', '花粉', '海鲜', '牛奶', '鸡蛋', '花生'],
    diseaseMap: {} as Record<string, boolean>,
    allergyMap: {} as Record<string, boolean>,
    customDisease: '',
    customAllergy: '',

    // Step 3: 用药
    medTypeOptions: MED_TYPE_OPTIONS,
    meds: [] as any[],
    newMed: {
      name: '',
      dosage: '',
      med_type: 'long_term',
      course_count: 1,
      days_per_course: '',
      course_days: '',
      times_per_day: 1,
    },
  },

  onLoad(options: any) {
    const mode = options.mode || 'onboard'
    const step = parseInt(options.step) || 1
    this.setData({ mode, step })

    // 编辑模式：加载现有数据
    if (mode === 'edit') {
      this.loadExistingProfile()
    }
  },

  /** 编辑模式下加载现有资料 */
  async loadExistingProfile() {
    try {
      const p: any = await profileApi.get()
      if (!p) return

      const medHistory = p.medical_history || []
      const allergies = p.allergies || []

      // 构建 map
      const diseaseMap: Record<string, boolean> = {}
      medHistory.forEach((d: string) => { diseaseMap[d] = true })
      const allergyMap: Record<string, boolean> = {}
      allergies.forEach((a: string) => { allergyMap[a] = true })

      this.setData({
        form: {
          real_name: p.real_name || '',
          gender: p.gender || '',
          birthday: p.birthday || '',
          blood_type: p.blood_type || '',
          medical_history: medHistory,
          allergies: allergies,
          emergency_contact_name: p.emergency_contact_name || '',
          emergency_contact_phone: p.emergency_contact_phone || '',
        },
        diseaseMap,
        allergyMap,
      })
    } catch (e) {
      console.warn('Load profile failed:', e)
    }
  },

  // ══════════════════════════════
  // Step 1 handlers
  // ══════════════════════════════

  onInput(e: any) {
    const key = e.currentTarget.dataset.key
    this.setData({ [`form.${key}`]: e.detail.value })
  },

  onGender(e: any) {
    this.setData({ 'form.gender': e.currentTarget.dataset.val })
  },

  onBirthday(e: any) {
    this.setData({ 'form.birthday': e.detail.value })
  },

  onBloodType(e: any) {
    this.setData({ 'form.blood_type': e.currentTarget.dataset.val })
  },

  // ══════════════════════════════
  // Step 2 handlers
  // ══════════════════════════════

  onToggleDisease(e: any) {
    const val = e.currentTarget.dataset.val
    const list = [...this.data.form.medical_history]
    const map = { ...this.data.diseaseMap }
    const idx = list.indexOf(val)
    if (idx >= 0) {
      list.splice(idx, 1)
      map[val] = false
    } else {
      list.push(val)
      map[val] = true
    }
    this.setData({ 'form.medical_history': list, diseaseMap: map })
  },

  onToggleAllergy(e: any) {
    const val = e.currentTarget.dataset.val
    const list = [...this.data.form.allergies]
    const map = { ...this.data.allergyMap }
    const idx = list.indexOf(val)
    if (idx >= 0) {
      list.splice(idx, 1)
      map[val] = false
    } else {
      list.push(val)
      map[val] = true
    }
    this.setData({ 'form.allergies': list, allergyMap: map })
  },

  onCustomDisease(e: any) {
    this.setData({ customDisease: e.detail.value })
  },

  onCustomAllergy(e: any) {
    this.setData({ customAllergy: e.detail.value })
  },

  // ══════════════════════════════
  // Step 3 handlers: 用药
  // ══════════════════════════════

  onMedInput(e: any) {
    const key = e.currentTarget.dataset.key
    this.setData({ [`newMed.${key}`]: e.detail.value })
  },

  onMedType(e: any) {
    this.setData({ 'newMed.med_type': e.currentTarget.dataset.val })
  },

  onTimesPerDay(e: any) {
    this.setData({ 'newMed.times_per_day': e.currentTarget.dataset.val })
  },

  onCourseCount(e: any) {
    this.setData({ 'newMed.course_count': e.currentTarget.dataset.val })
  },

  onAddMed() {
    const { name, dosage, med_type, course_count, days_per_course, course_days, times_per_day } = this.data.newMed

    if (!name.trim()) {
      wx.showToast({ title: '请输入药品名称', icon: 'none' })
      return
    }

    if (med_type === 'course' && !days_per_course) {
      wx.showToast({ title: '请输入每个疗程天数', icon: 'none' })
      return
    }

    if (med_type === 'temporary' && !course_days) {
      wx.showToast({ title: '请输入服用天数', icon: 'none' })
      return
    }

    const typeLabel = MED_TYPE_OPTIONS.find(o => o.val === med_type)?.label || ''
    const timesLabel = `每天${times_per_day}次`

    let totalDays = 0
    let detailLabel = ''
    if (med_type === 'course') {
      totalDays = (course_count || 1) * (parseInt(days_per_course as string) || 0)
      detailLabel = `${course_count}个疗程×${days_per_course}天`
    } else if (med_type === 'temporary') {
      totalDays = parseInt(course_days as string) || 7
      detailLabel = `${totalDays}天`
    } else {
      detailLabel = '长期'
    }

    const meds = [...this.data.meds, {
      name: name.trim(),
      dosage: dosage.trim(),
      med_type,
      totalDays,
      course_count: course_count || 1,
      days_per_course: parseInt(days_per_course as string) || 0,
      times_per_day,
      typeLabel,
      timesLabel,
      detailLabel,
    }]

    this.setData({
      meds,
      newMed: { name: '', dosage: '', med_type: 'long_term', course_count: 1, days_per_course: '', course_days: '', times_per_day: 1 },
    })

    wx.showToast({ title: '已添加', icon: 'success' })
  },

  onRemoveMed(e: any) {
    const idx = e.currentTarget.dataset.idx
    const meds = [...this.data.meds]
    meds.splice(idx, 1)
    this.setData({ meds })
  },

  // ══════════════════════════════
  // Navigation
  // ══════════════════════════════

  onNext() {
    const { step, mode } = this.data

    // Step 1 validation (both modes)
    if (step === 1) {
      if (!this.data.form.real_name.trim()) {
        wx.showToast({ title: '请输入姓名', icon: 'none' })
        return
      }
      if (!this.data.form.gender) {
        wx.showToast({ title: '请选择性别', icon: 'none' })
        return
      }
    }

    // 编辑模式：保存当前步骤后返回
    if (mode === 'edit') {
      this.saveCurrentStep()
      return
    }

    // 引导模式：下一步或完成
    if (step < 4) {
      if (step === 2) this.mergeCustomInputs()
      this.setData({ step: step + 1 })
    } else {
      this.submitAll()
    }
  },

  onBack() {
    if (this.data.mode === 'edit') {
      wx.navigateBack()
      return
    }
    if (this.data.step > 1) {
      this.setData({ step: this.data.step - 1 })
    }
  },

  onSkip() {
    const { step, mode } = this.data

    if (mode === 'edit') {
      wx.navigateBack()
      return
    }

    if (step < 4) {
      this.setData({ step: step + 1 })
    } else {
      this.submitAll()
    }
  },

  mergeCustomInputs() {
    const history = [...this.data.form.medical_history]
    const allergies = [...this.data.form.allergies]
    const diseaseMap = { ...this.data.diseaseMap }
    const allergyMap = { ...this.data.allergyMap }

    if (this.data.customDisease.trim()) {
      const custom = this.data.customDisease.split(/[,，、]/).map(s => s.trim()).filter(Boolean)
      custom.forEach(c => { if (!history.includes(c)) { history.push(c); diseaseMap[c] = true } })
    }
    if (this.data.customAllergy.trim()) {
      const custom = this.data.customAllergy.split(/[,，、]/).map(s => s.trim()).filter(Boolean)
      custom.forEach(c => { if (!allergies.includes(c)) { allergies.push(c); allergyMap[c] = true } })
    }

    this.setData({
      'form.medical_history': history,
      'form.allergies': allergies,
      diseaseMap,
      allergyMap,
    })
  },

  /** 编辑模式：只保存当前步骤的字段 */
  async saveCurrentStep() {
    const { step, form } = this.data
    this.setData({ saving: true })

    try {
      if (step === 2) this.mergeCustomInputs()

      const payload: any = {}
      if (step === 1) {
        payload.real_name = form.real_name || null
        payload.gender = form.gender || null
        payload.birthday = form.birthday || null
        payload.blood_type = form.blood_type === '不确定' ? null : form.blood_type || null
      } else if (step === 2) {
        payload.medical_history = this.data.form.medical_history.length ? this.data.form.medical_history : null
        payload.allergies = this.data.form.allergies.length ? this.data.form.allergies : null
      } else if (step === 4) {
        payload.emergency_contact_name = form.emergency_contact_name || null
        payload.emergency_contact_phone = form.emergency_contact_phone || null
      }

      await profileApi.update(payload)

      this.setData({ saving: false })
      wx.showToast({ title: '已保存', icon: 'success' })

      setTimeout(() => wx.navigateBack(), 600)
    } catch (err) {
      this.setData({ saving: false })
      console.error('Save step failed:', err)
      wx.showToast({ title: '保存失败', icon: 'none' })
    }
  },

  /** 引导模式：提交所有数据 */
  async submitAll() {
    this.setData({ saving: true })

    try {
      // 1. 保存个人资料
      const { form } = this.data
      await profileApi.update({
        real_name: form.real_name || null,
        gender: form.gender || null,
        birthday: form.birthday || null,
        blood_type: form.blood_type === '不确定' ? null : form.blood_type || null,
        medical_history: form.medical_history.length ? form.medical_history : null,
        allergies: form.allergies.length ? form.allergies : null,
        emergency_contact_name: form.emergency_contact_name || null,
        emergency_contact_phone: form.emergency_contact_phone || null,
      })

      // 2. 创建药物记录
      const today = todayStr()
      for (const med of this.data.meds) {
        let endDate: string | null = null
        if (med.med_type === 'course' && med.totalDays > 0) {
          endDate = calcEndDate(today, med.totalDays)
        } else if (med.med_type === 'temporary') {
          endDate = calcEndDate(today, med.totalDays || 7)
        }

        await medsApi.create({
          name: med.name,
          dosage: med.dosage || null,
          frequency: med.detailLabel + ' ' + med.timesLabel,
          scheduled_times: DEFAULT_TIMES[med.times_per_day] || ['08:00'],
          start_date: today,
          end_date: endDate,
        })
      }

      this.setData({ saving: false })
      wx.showToast({ title: '资料已保存', icon: 'success' })

      setTimeout(() => {
        wx.switchTab({ url: '/pages/home/home' })
      }, 800)
    } catch (err) {
      this.setData({ saving: false })
      console.error('Onboarding submit failed:', err)
      wx.showToast({ title: '保存失败', icon: 'none' })
    }
  },
})

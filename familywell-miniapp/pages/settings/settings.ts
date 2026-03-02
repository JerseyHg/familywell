/**
 * pages/settings/settings.ts — 设置页
 * ═══════════════════════════════════════
 * ★ 新增：帮助与反馈、导出数据、关于 FamilyWell 功能
 */
import { profileApi, familyApi, recordsApi, authApi } from '../../services/api'

Page({
  data: {
    // 紧急信息
    showEmergencyModal: false,
    emergency: {
      name: '',
      bloodType: '',
      age: '',
      allergies: '',
      diseases: '',
      medications: '',
    },

    // 家庭
    family: null as any,
    myRole: '' as string,
    familyMembers: [] as any[],

    // 加入家庭弹窗
    showJoinModal: false,
    joinCode: '',
    joining: false,

    // 数据
    recordCount: 0,

    // ★ 关于弹窗
    showAboutModal: false,
  },

  onShow() {
    this.getTabBar()?.setData({ active: 3 })
    this.loadData()
  },

  async loadData() {
    try {
      const [profile, family, records] = await Promise.all([
        profileApi.get().catch(() => null),
        familyApi.mine().catch(() => null),
        recordsApi.list({ page: 1, size: 1 }).catch(() => null),
      ])

      const p = profile as any
      this.setData({
        emergency: {
          name: p?.real_name || '',
          bloodType: p?.blood_type || '',
          age: p?.age || '',
          allergies: (p?.allergies || []).join('、'),
          diseases: (p?.medical_history || []).join('·'),
          medications: (p?.active_medications || []).join('·'),
        },
        recordCount: (records as any)?.total || 0,
      })

      // 加载家庭信息
      if (family) {
        this.setData({ family })
        await this.loadFamilyMembers((family as any).id)
      } else {
        this.setData({ family: null, myRole: '', familyMembers: [] })
      }
    } catch (err) {
      console.error('Settings load failed:', err)
    }
  },

  async loadFamilyMembers(familyId: number) {
    try {
      const members: any = await familyApi.members(familyId)
      // 判断当前用户角色
      const myInfo = wx.getStorageSync('user')

      const myMember = (members as any[]).find((m: any) => {
        return m.user_id === myInfo?.id
      })

      this.setData({
        familyMembers: members || [],
        myRole: myMember?.role || 'member',
      })
    } catch (err) {
      console.error('Load family members failed:', err)
    }
  },

  // ── 紧急信息 ──

  showEmergency() {
    this.setData({ showEmergencyModal: true })
  },

  hideEmergency() {
    this.setData({ showEmergencyModal: false })
  },

  // ══════════════════════════════
  // 家庭管理
  // ══════════════════════════════

  // ── 创建家庭 ──
  onCreateFamily() {
    wx.showModal({
      title: '创建家庭',
      editable: true,
      placeholderText: '输入家庭名称（选填）',
      success: async (res) => {
        if (res.confirm) {
          try {
            wx.showLoading({ title: '创建中...' })
            const family: any = await familyApi.create(res.content || undefined)
            wx.hideLoading()
            wx.showToast({ title: '创建成功', icon: 'success' })
            this.setData({ family, myRole: 'admin' })
            await this.loadFamilyMembers(family.id)
          } catch (e) {
            wx.hideLoading()
            console.error('Create family failed:', e)
          }
        }
      },
    })
  },

  // ── 加入家庭 ──
  noop() {},

  onShowJoinModal() {
    this.setData({ showJoinModal: true, joinCode: '' })
  },

  hideJoinModal() {
    this.setData({ showJoinModal: false })
  },

  onJoinCodeInput(e: any) {
    this.setData({ joinCode: e.detail.value.toUpperCase() })
  },

  async onSubmitJoin() {
    const code = this.data.joinCode.trim()
    if (!code) {
      wx.showToast({ title: '请输入邀请码', icon: 'none' })
      return
    }

    this.setData({ joining: true })
    try {
      await familyApi.join(code)
      wx.showToast({ title: '加入成功', icon: 'success' })
      this.setData({ showJoinModal: false, joining: false })
      // 重新加载
      this.loadData()
    } catch (e) {
      this.setData({ joining: false })
      console.error('Join family failed:', e)
    }
  },

  // ── 复制邀请码 ──
  copyInviteCode() {
    if (this.data.family?.invite_code) {
      wx.setClipboardData({
        data: this.data.family.invite_code,
        success: () => wx.showToast({ title: '已复制', icon: 'success' }),
      })
    }
  },

  // ── 查看成员概况（admin） ──
  onViewMember(e: any) {
    const uid = e.currentTarget.dataset.uid
    const name = e.currentTarget.dataset.name
    wx.showToast({ title: `查看 ${name} 的健康档案`, icon: 'none' })
  },

  // ── 移除成员（admin） ──
  onRemoveMember(e: any) {
    const uid = e.currentTarget.dataset.uid
    const name = e.currentTarget.dataset.name

    wx.showModal({
      title: '移除成员',
      content: `确定要将 ${name} 移出家庭吗？`,
      confirmColor: '#E85D3A',
      success: async (res) => {
        if (res.confirm) {
          try {
            await familyApi.removeMember(this.data.family.id, uid)
            wx.showToast({ title: '已移除', icon: 'success' })
            await this.loadFamilyMembers(this.data.family.id)
          } catch (e) {
            console.error('Remove member failed:', e)
          }
        }
      },
    })
  },

  // ── 编辑个人信息 ──
  goEditProfile(e: any) {
    const step = e.currentTarget.dataset.step
    wx.navigateTo({ url: `/pages/onboarding/onboarding?mode=edit&step=${step}` })
  },

  // ── 跳转归档 ──
  goArchive() {
    wx.switchTab({ url: '/pages/archive/archive' })
  },

  // ══════════════════════════════
  // ★ 帮助与反馈
  // ══════════════════════════════
  onFeedback() {
    wx.showModal({
      title: '帮助与反馈',
      content: '如有问题或建议，请通过以下方式联系我们：\n\n邮箱：teban.official@gmail.com\n\n我们会尽快回复您！',
      showCancel: false,
      confirmText: '我知道了',
    })
  },

  // ══════════════════════════════
  // ★ 导出数据
  // ══════════════════════════════
  onExportData() {
    wx.showModal({
      title: '导出数据',
      content: '数据导出功能正在开发中，敬请期待！\n\n上线后您可以导出所有健康档案、用药记录等数据。',
      showCancel: false,
      confirmText: '我知道了',
    })
  },

  // ══════════════════════════════
  // ★ 关于 FamilyWell
  // ══════════════════════════════
  onShowAbout() {
    this.setData({ showAboutModal: true })
  },

  hideAbout() {
    this.setData({ showAboutModal: false })
  },

  copyEmail() {
    wx.setClipboardData({
      data: 'teban.official@gmail.com',
      success: () => wx.showToast({ title: '邮箱已复制', icon: 'success' }),
    })
  },

  // ── 退出登录 ──
  onLogout() {
    wx.showModal({
      title: '确认退出',
      content: '退出后需要重新登录',
      success: (res) => {
        if (res.confirm) {
          wx.removeStorageSync('token')
          wx.removeStorageSync('userInfo')
          wx.redirectTo({ url: '/pages/login/login' })
        }
      },
    })
  },

  // ── ★ 跳转协议页 ──
  goAgreement(e: any) {
    const type = e.currentTarget.dataset.type || 'service'
    wx.navigateTo({ url: `/pages/agreement/agreement?type=${type}` })
  },

  // ── ★ 注销账号 ──
  onDeleteAccount() {
    wx.showModal({
      title: '注销账号',
      content: '注销后，您的所有健康档案、用药记录、对话历史等数据将被永久删除且无法恢复。确定要注销吗？',
      confirmText: '确定注销',
      confirmColor: '#E85D3A',
      success: (res) => {
        if (res.confirm) {
          // 二次确认
          wx.showModal({
            title: '再次确认',
            content: '此操作不可逆，所有数据将被永久删除。是否继续？',
            confirmText: '永久删除',
            confirmColor: '#E85D3A',
            success: async (res2) => {
              if (res2.confirm) {
                try {
                  wx.showLoading({ title: '注销中...', mask: true })
                  await authApi.deleteAccount()
                  wx.hideLoading()

                  wx.removeStorageSync('token')
                  wx.removeStorageSync('user')
                  wx.removeStorageSync('userInfo')

                  wx.showToast({ title: '账号已注销', icon: 'success' })
                  setTimeout(() => {
                    wx.redirectTo({ url: '/pages/login/login' })
                  }, 1000)
                } catch (err: any) {
                  wx.hideLoading()
                  wx.showToast({ title: err.message || '注销失败', icon: 'none' })
                }
              }
            },
          })
        }
      },
    })
  },
})

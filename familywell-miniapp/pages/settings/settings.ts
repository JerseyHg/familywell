import { profileApi, familyApi, recordsApi, authApi } from '../../services/api'

Page({
  data: {
    showEmergencyModal: false,
    emergency: {
      name: '',
      bloodType: '',
      age: '',
      allergies: '',
      diseases: '',
      medications: '',
    },
    familyMembers: [] as any[],
    inviteCode: '',
    recordCount: 0,
  },

  onShow() {
    this.getTabBar()?.setData({ active: 3 })
    this.loadData()
  },

  async loadData() {
    try {
      const [profile, family, records] = await Promise.all([
        profileApi.get(),
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
        inviteCode: (family as any)?.invite_code || '',
        familyMembers: (family as any)?.members || [],
        recordCount: (records as any)?.total || 0,
      })
    } catch (err) {
      console.error('Settings load failed:', err)
    }
  },

  showEmergency() {
    this.setData({ showEmergencyModal: true })
  },

  hideEmergency() {
    this.setData({ showEmergencyModal: false })
  },

  copyInviteCode() {
    if (this.data.inviteCode) {
      wx.setClipboardData({ data: this.data.inviteCode })
    }
  },

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
})

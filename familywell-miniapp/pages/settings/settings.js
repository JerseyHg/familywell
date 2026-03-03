"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
var api_1 = require("../../services/api");

Page({
  data: {
    isLoggedIn: false,
    showEmergencyModal: false,
    emergency: {
      name: '',
      bloodType: '',
      age: '',
      allergies: '',
      diseases: '',
      medications: '',
    },
    family: null,
    myRole: '',
    familyMembers: [],
    showJoinModal: false,
    joinCode: '',
    joining: false,
    recordCount: 0,
    showAboutModal: false,
  },

  onShow: function () {
    var tabBar = this.getTabBar();
    if (tabBar) tabBar.setData({ active: 3 });

    var token = wx.getStorageSync('token');
    var isLoggedIn = !!token;
    this.setData({ isLoggedIn: isLoggedIn });

    if (isLoggedIn) {
      this.loadData();
    }
  },

  _requireLogin: function () {
    if (!this.data.isLoggedIn) {
      wx.showModal({
        title: '需要登录',
        content: '请先登录后再使用此功能',
        confirmText: '去登录',
        cancelText: '取消',
        success: function (res) {
          if (res.confirm) wx.navigateTo({ url: '/pages/login/login' });
        },
      });
      return false;
    }
    return true;
  },

  goLogin: function () {
    wx.navigateTo({ url: '/pages/login/login' });
  },

  loadData: function () {
    var self = this;
    Promise.all([
      api_1.profileApi.get().catch(function () { return null; }),
      api_1.familyApi.mine().catch(function () { return null; }),
      api_1.recordsApi.list({ page: 1, size: 1 }).catch(function () { return null; }),
    ]).then(function (results) {
      var profile = results[0];
      var family = results[1];
      var records = results[2];

      var p = profile || {};
      self.setData({
        emergency: {
          name: p.real_name || '',
          bloodType: p.blood_type || '',
          age: p.age || '',
          allergies: (p.allergies || []).join('、'),
          diseases: (p.medical_history || []).join('·'),
          medications: (p.active_medications || []).join('·'),
        },
        recordCount: (records && records.total) || 0,
      });

      if (family) {
        self.setData({ family: family });
        self.loadFamilyMembers(family.id);
      } else {
        self.setData({ family: null, myRole: '', familyMembers: [] });
      }
    }).catch(function (err) {
      console.error('Settings load failed:', err);
    });
  },

  loadFamilyMembers: function (familyId) {
    var self = this;
    api_1.familyApi.members(familyId).then(function (members) {
      var myInfo = wx.getStorageSync('user');
      var myMember = (members || []).find(function (m) {
        return m.user_id === (myInfo && myInfo.id);
      });
      self.setData({
        familyMembers: members || [],
        myRole: (myMember && myMember.role) || 'member',
      });
    }).catch(function (err) {
      console.error('Load family members failed:', err);
    });
  },

  showEmergency: function () {
    if (!this._requireLogin()) return;
    this.setData({ showEmergencyModal: true });
  },

  hideEmergency: function () {
    this.setData({ showEmergencyModal: false });
  },

  onCreateFamily: function () {
    if (!this._requireLogin()) return;
    var self = this;
    wx.showModal({
      title: '创建家庭',
      editable: true,
      placeholderText: '输入家庭名称（选填）',
      success: function (res) {
        if (res.confirm) {
          wx.showLoading({ title: '创建中...' });
          api_1.familyApi.create(res.content || undefined).then(function (family) {
            wx.hideLoading();
            wx.showToast({ title: '创建成功', icon: 'success' });
            self.setData({ family: family, myRole: 'admin' });
            self.loadFamilyMembers(family.id);
          }).catch(function () {
            wx.hideLoading();
          });
        }
      },
    });
  },

  noop: function () {},

  onShowJoinModal: function () {
    if (!this._requireLogin()) return;
    this.setData({ showJoinModal: true, joinCode: '' });
  },

  hideJoinModal: function () {
    this.setData({ showJoinModal: false });
  },

  onJoinCodeInput: function (e) {
    this.setData({ joinCode: e.detail.value.toUpperCase() });
  },

  onSubmitJoin: function () {
    var self = this;
    var code = this.data.joinCode.trim();
    if (!code) {
      wx.showToast({ title: '请输入邀请码', icon: 'none' });
      return;
    }
    this.setData({ joining: true });
    api_1.familyApi.join(code).then(function () {
      wx.showToast({ title: '加入成功', icon: 'success' });
      self.setData({ showJoinModal: false, joining: false });
      self.loadData();
    }).catch(function () {
      self.setData({ joining: false });
    });
  },

  copyInviteCode: function () {
    if (this.data.family && this.data.family.invite_code) {
      wx.setClipboardData({
        data: this.data.family.invite_code,
        success: function () { wx.showToast({ title: '已复制', icon: 'success' }); },
      });
    }
  },

  onRemoveMember: function (e) {
    if (!this._requireLogin()) return;
    var self = this;
    var userId = e.currentTarget.dataset.userid;
    if (!userId || !this.data.family) return;
    wx.showModal({
      title: '移除成员',
      content: '确定要将该成员移出家庭吗？',
      success: function (res) {
        if (res.confirm) {
          api_1.familyApi.removeMember(self.data.family.id, userId).then(function () {
            wx.showToast({ title: '已移除', icon: 'success' });
            self.loadFamilyMembers(self.data.family.id);
          }).catch(function () {});
        }
      },
    });
  },

  goEditProfile: function (e) {
    if (!this._requireLogin()) return;
    var step = (e.currentTarget.dataset && e.currentTarget.dataset.step) || 1;
    wx.navigateTo({ url: '/pages/onboarding/onboarding?mode=edit&step=' + step });
  },

  goHelpFeedback: function () {
    wx.showModal({
      title: '帮助与反馈',
      content: '如有问题或建议，请联系：support@familywell.cn',
      showCancel: false,
    });
  },

  exportData: function () {
    if (!this._requireLogin()) return;
    wx.showModal({
      title: '导出数据',
      content: '您共有 ' + this.data.recordCount + ' 条记录。导出功能即将上线，敬请期待。',
      showCancel: false,
    });
  },

  showAbout: function () {
    this.setData({ showAboutModal: true });
  },

  hideAbout: function () {
    this.setData({ showAboutModal: false });
  },

  onLogout: function () {
    wx.showModal({
      title: '退出登录',
      content: '确定要退出登录吗？',
      success: function (res) {
        if (res.confirm) {
          wx.removeStorageSync('token');
          wx.removeStorageSync('user');
          wx.reLaunch({ url: '/pages/home/home' });
        }
      },
    });
  },

  onDeleteAccount: function () {
    var self = this;
    if (!this._requireLogin()) return;
    wx.showModal({
      title: '注销账号',
      content: '注销后所有数据将被永久删除，且无法恢复。确定继续？',
      confirmColor: '#e53e3e',
      success: function (res) {
        if (res.confirm) {
          api_1.authApi.deleteAccount().then(function () {
            wx.removeStorageSync('token');
            wx.removeStorageSync('user');
            wx.showToast({ title: '账号已注销', icon: 'none' });
            setTimeout(function () { wx.reLaunch({ url: '/pages/home/home' }); }, 1000);
          }).catch(function () {
            wx.showToast({ title: '注销失败', icon: 'none' });
          });
        }
      },
    });
  },
});

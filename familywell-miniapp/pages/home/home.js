"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
var api_1 = require("../../services/api");
var upload_1 = require("../../services/upload");

Page({
  data: {
    isLoggedIn: false,
    avatarText: '👤',
    profile: { nickname: '', age: null, tags: [] },
    pendingTasks: [],
    aiTip: '',
    recentActivity: [],
    alertCount: 0,
    medSuggestions: [],
    prompts: [
      { icon: '🍽️', text: '过去7天饮食情况' },
      { icon: '💉', text: '血压最近趋势怎样' },
      { icon: '💊', text: '这周药吃齐了吗' },
      { icon: '📋', text: '最近身体怎么样' },
    ],
    greeting: '',

    showVoiceModal: false,
    voiceSegments: [],
    isRecording: false,
    recordingDuration: 0,
  },

  _recorder: null,
  _recordTimer: null,
  _stopFallbackTimer: null,
  _recordStartTime: 0,
  _pendingStop: false,
  _recorderBusy: false,

  // ════════════════════════════════════════
  //  生命周期
  // ════════════════════════════════════════

  onLoad: function () {
    var hour = new Date().getHours();
    var greeting = '你好';
    if (hour < 6) greeting = '夜深了';
    else if (hour < 12) greeting = '早上好';
    else if (hour < 18) greeting = '下午好';
    else greeting = '晚上好';
    this.setData({ greeting: greeting });
  },

  onShow: function () {
    var token = wx.getStorageSync('token');
    var user = wx.getStorageSync('user');
    var isLoggedIn = !!token;

    this.setData({
      isLoggedIn: isLoggedIn,
      avatarText: (user && user.nickname) ? user.nickname.slice(0, 1) : '👤',
      'profile.nickname': (user && user.nickname) ? user.nickname : '',
    });

    var tabBar = this.getTabBar();
    if (tabBar) tabBar.setData({ active: 0 });

    if (isLoggedIn) {
      this.checkOnboarding();
      this.loadHomeData();
    }
  },

  onPullDownRefresh: function () {
    var self = this;
    if (this.data.isLoggedIn) {
      this.loadHomeData()
        .then(function () { wx.stopPullDownRefresh(); })
        .catch(function () { wx.stopPullDownRefresh(); });
    } else {
      wx.stopPullDownRefresh();
    }
  },

  // ════════════════════════════════════════
  //  ★ 登录相关
  // ════════════════════════════════════════

  onLoginTap: function () {
    if (this.data.isLoggedIn) {
      wx.switchTab({ url: '/pages/settings/settings' });
    } else {
      wx.navigateTo({ url: '/pages/login/login' });
    }
  },

  goLogin: function () {
    wx.navigateTo({ url: '/pages/login/login' });
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

  // ════════════════════════════════════════
  //  数据加载
  // ════════════════════════════════════════

  checkOnboarding: function () {
    api_1.profileApi.get().then(function (profile) {
      if (profile && !profile.onboarding_completed) {
        wx.redirectTo({ url: '/pages/onboarding/onboarding' });
      }
    }).catch(function () {});
  },

  loadHomeData: function () {
    var self = this;
    return Promise.all([
      api_1.homeApi.getData(),
      api_1.profileApi.get(),
    ]).then(function (results) {
      var homeData = results[0] || {};
      var profileData = results[1] || {};

      var profile = {
        nickname: profileData.real_name || profileData.nickname || (wx.getStorageSync('user') || {}).nickname || '',
        age: profileData.age,
        tags: [],
      };
      if (profileData.blood_type) profile.tags.push(profileData.blood_type + '型血');
      if (profileData.allergies && profileData.allergies.length) {
        profile.tags.push('过敏: ' + profileData.allergies.join('、'));
      }

      var recentActivity = (homeData.recent_activity || []).map(function (r) {
        return {
          id: r.id,
          category: r.category,
          title: r.title || '未命名',
          date: r.record_date ? self._formatLocalDate(r.record_date) : '',
          ai_status: r.ai_status,
        };
      });

      self.setData({
        profile: profile,
        avatarText: profile.nickname ? profile.nickname.slice(0, 1) : '👤',
        pendingTasks: (homeData.pending_tasks || []).map(function (t) {
          return Object.assign({}, t, { completing: false });
        }),
        aiTip: homeData.ai_tip || '',
        recentActivity: recentActivity.slice(0, 5),
        alertCount: homeData.alert_count || 0,
        medSuggestions: homeData.medication_suggestions || [],
      });
    }).catch(function (err) {
      console.error('loadHomeData error:', err);
    });
  },

  _formatLocalDate: function (dateStr) {
    if (!dateStr) return '';
    var d = new Date(dateStr);
    var now = new Date();
    var dLocal = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var nowLocal = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var diff = Math.round((nowLocal.getTime() - dLocal.getTime()) / 86400000);
    if (diff === 0) return '今天';
    if (diff === 1) return '昨天';
    return (d.getMonth() + 1) + '/' + d.getDate();
  },

  // ════════════════════════════════════════
  //  导航 & 操作
  // ════════════════════════════════════════

  onUpload: function () {
    if (!this._requireLogin()) return;
    this.onPhotoAdd();
  },

  onPhotoAdd: function () {
    var self = this;
    if (!this._requireLogin()) return;
    (0, upload_1.batchUpload)({ maxCount: 9 })
      .then(function (result) {
        (0, upload_1.pollBatchAIStatus)(result.recordIds, function () { self.loadHomeData(); });
      })
      .catch(function () {});
  },

  onPromptTap: function (e) {
    var text = e.currentTarget.dataset.text;
    var app = getApp();
    app.globalData = app.globalData || {};
    app.globalData.chatInitQuestion = text;
    wx.switchTab({ url: '/pages/chat/chat' });
  },

  onActivityTap: function (e) {
    var item = e.currentTarget.dataset.item;
    if (item && item.id && !String(item.id).startsWith('temp_')) {
      wx.navigateTo({ url: '/pages/record-detail/record-detail?id=' + item.id });
    }
  },

  // ════════════════════════════════════════
  //  打卡
  // ════════════════════════════════════════

  onCompleteTask: function (e) {
    var self = this;
    var taskId = e.currentTarget.dataset.id;
    var idx = this.data.pendingTasks.findIndex(function (t) { return t.id === taskId; });
    if (idx === -1) return;
    this.setData({ ['pendingTasks[' + idx + '].completing']: true });
    api_1.medsApi.completeTask(taskId).then(function () {
      var tasks = self.data.pendingTasks.filter(function (_, i) { return i !== idx; });
      self.setData({ pendingTasks: tasks });
      wx.showToast({ title: '✅ 已打卡', icon: 'none' });
    }).catch(function () {
      self.setData({ ['pendingTasks[' + idx + '].completing']: false });
      wx.showToast({ title: '打卡失败', icon: 'none' });
    });
  },

  onToggleTask: function (e) {
    var completed = e.currentTarget.dataset.completed;
    if (!completed) this.onCompleteTask(e);
  },

  // ════════════════════════════════════════
  //  药物建议
  // ════════════════════════════════════════

  onConfirmSuggestion: function (e) {
    var item = e.currentTarget.dataset.item || { id: e.currentTarget.dataset.id };
    if (!item || !item.id) return;
    wx.navigateTo({ url: '/pages/confirm-med/confirm-med?id=' + item.id + '&name=' + encodeURIComponent(item.name || '') });
  },

  onDismissSuggestion: function (e) {
    var self = this;
    var item = e.currentTarget.dataset.item || { id: e.currentTarget.dataset.id };
    if (!item || !item.id) return;
    api_1.medsApi.dismissSuggestion(item.id).then(function () {
      self.setData({ medSuggestions: self.data.medSuggestions.filter(function (s) { return s.id !== item.id; }) });
      wx.showToast({ title: '已忽略', icon: 'none' });
    }).catch(function () {
      wx.showToast({ title: '操作失败', icon: 'none' });
    });
  },

  // ════════════════════════════════════════
  //  ★ 语音录音
  // ════════════════════════════════════════

  onVoiceAdd: function () {
    if (!this._requireLogin()) return;
    this.setData({ showVoiceModal: true, voiceSegments: [], isRecording: false, recordingDuration: 0 });
  },

  hideVoiceModal: function () {
    if (this.data.isRecording) { if (this._recorder) this._recorder.stop(); }
    this.setData({ showVoiceModal: false });
  },

  noop: function () {},

  onRemoveSegment: function (e) {
    var index = e.currentTarget.dataset.index;
    var segs = this.data.voiceSegments.slice();
    segs.splice(index, 1);
    this.setData({ voiceSegments: segs });
  },

  // ── ★ 按住说话：立即开始 ──
  onRecordStart: function () {
    if (this.data.isRecording || this._recorderBusy) return;
    this._pendingStop = false;
    this._startRecording();
  },

  // ── ★ 松开结束 ──
  onRecordEnd: function () {
    if (this.data.isRecording) {
      this._recorderBusy = true;
      this._stopRecording();
    } else {
      // recorder.start() 是异步的，onStart 还没回调
      this._pendingStop = true;
    }
  },

  _initRecorder: function () {
    if (this._recorder) return;
    var self = this;
    var recorder = wx.getRecorderManager();

    recorder.onStart(function () {
      self._recorderBusy = false;
      self._recordStartTime = Date.now();
      self.setData({ isRecording: true, recordingDuration: 0 });
      self._recordTimer = setInterval(function () {
        self.setData({ recordingDuration: self.data.recordingDuration + 1 });
      }, 1000);

      // ★ 用户在 onStart 前就松手了 → 立即停止
      if (self._pendingStop) {
        self._pendingStop = false;
        self._recorder.stop();
      }
    });

    recorder.onStop(function (res) {
      clearInterval(self._recordTimer);
      clearTimeout(self._stopFallbackTimer);
      self._recorderBusy = false;
      var duration = Math.round((Date.now() - self._recordStartTime) / 1000);
      self.setData({ isRecording: false });

      if (duration >= 1 && res.tempFilePath) {
        var segs = self.data.voiceSegments.slice();
        segs.push({ duration: duration, tempFilePath: res.tempFilePath });
        self.setData({ voiceSegments: segs });
      } else if (duration < 1) {
        wx.showToast({ title: '录音太短', icon: 'none' });
      }
    });

    recorder.onError(function (err) {
      console.error('[Voice] recorder error:', err);
      clearInterval(self._recordTimer);
      clearTimeout(self._stopFallbackTimer);
      self._recorderBusy = false;
      self.setData({ isRecording: false });
      wx.showToast({ title: '录音失败，请重试', icon: 'none' });
    });

    self._recorder = recorder;
  },

  _startRecording: function () {
    var self = this;
    this._initRecorder();
    wx.authorize({
      scope: 'scope.record',
      success: function () {
        self._recorderBusy = true;
        self._recorder.start({
          format: 'mp3',
          sampleRate: 16000,
          numberOfChannels: 1,
          encodeBitRate: 48000,
          duration: 60000,
        });
        self._stopFallbackTimer = setTimeout(function () {
          if (self.data.isRecording) self._stopRecording();
        }, 55000);
      },
      fail: function () {
        wx.showModal({
          title: '需要录音权限',
          content: '请在设置中允许使用麦克风',
          confirmText: '去设置',
          success: function (r) { if (r.confirm) wx.openSetting(); },
        });
      },
    });
  },

  _stopRecording: function () {
    clearTimeout(this._stopFallbackTimer);
    if (this._recorder) this._recorder.stop();
  },

  // ── ★ 提交 ──
  onSubmitVoice: function () {
    var self = this;
    var segs = this.data.voiceSegments;
    if (segs.length === 0 || this.data.isRecording) {
      wx.showToast({ title: '请先录音', icon: 'none' });
      return;
    }

    var tempId = 'temp_' + Date.now();
    var now = new Date();
    self.setData({
      showVoiceModal: false,
      voiceSegments: [],
      recentActivity: [{
        id: tempId, category: 'other',
        title: '语音记录处理中...',
        date: (now.getMonth() + 1) + '/' + now.getDate(),
        ai_status: 'processing',
      }].concat(self.data.recentActivity).slice(0, 5),
    });

    var audioKeys = [];
    var uploadNext = function (i) {
      if (i >= segs.length) {
        // 全部上传完成，调用后端
        api_1.medsApi.voiceAddAudio(audioKeys).then(function (res) {
          var items = (res && res.items) || [];
          var typeIcons = { medication: '💊', food: '🍽️', vitals: '❤️', symptom: '📝', insurance: '🛡️', memo: '📋' };
          if (items.length > 0) {
            var labels = items.map(function (i) { return (typeIcons[i.type] || '✅') + (i.summary || ''); }).join(' ');
            wx.showToast({ title: labels.slice(0, 40) || '✅ 记录成功', icon: 'none', duration: 2500 });
          } else {
            wx.showToast({ title: '✅ 记录成功', icon: 'none' });
          }
          self.loadHomeData();
        }).catch(function (err) {
          self.setData({
            recentActivity: self.data.recentActivity.map(function (item) {
              if (item.id === tempId) return Object.assign({}, item, { ai_status: 'failed' });
              return item;
            }),
          });
          wx.showToast({ title: (err && err.message) || '记录失败', icon: 'none' });
        });
        return;
      }
      (0, upload_1.uploadAudioToCOS)(segs[i].tempFilePath).then(function (result) {
        audioKeys.push(result.fileKey);
        uploadNext(i + 1);
      }).catch(function (err) {
        wx.showToast({ title: '上传失败', icon: 'none' });
      });
    };
    uploadNext(0);
  },

  onRetryVoice: function () {
    this.setData({ showVoiceModal: true, voiceSegments: [] });
  },
});

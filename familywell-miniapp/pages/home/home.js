"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
var api_1 = require("../../services/api");
var upload_1 = require("../../services/upload");
var cache_1 = require("../../services/cache");

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

    // ★ 首页仪表盘
    dashAlerts: [],
    dashIndicators: [],
    dashMedAdherence: null,
    dashNutrition: null,

    showVoiceModal: false,
    voiceSegments: [],
    isRecording: false,
    recordingDuration: 0,

    // ★ 药物确认弹窗
    showMedConfirm: false,
    medConfirmId: null,
    medConfirmName: '',
    medConfirmDosage: '',
    medConfirmType: 'long_term',
    medConfirmTimes: 1,
    medConfirmDays: '',
    medConfirmSubmitting: false,
  },

  _recorder: null,
  _recordTimer: null,
  _stopFallbackTimer: null,
  _recordStartTime: 0,
  _pendingStop: false,
  _recorderBusy: false,
  _startSafetyTimer: null,
  _stopSafetyTimer: null,

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

    // ★ 立即用缓存的 profile 数据显示（避免闪烁）
    var cachedProfile = cache_1.getCached(cache_1.CACHE_KEYS.PROFILE);
    var displayName = '';
    if (cachedProfile && (cachedProfile.real_name || cachedProfile.nickname)) {
      displayName = cachedProfile.real_name || cachedProfile.nickname;
    } else if (user && user.nickname) {
      displayName = user.nickname;
    }

    this.setData({
      isLoggedIn: isLoggedIn,
      avatarText: displayName ? displayName.slice(0, 1) : '👤',
      'profile.nickname': displayName,
    });

    // ★ 立即用缓存的 home 数据渲染（stale-while-revalidate 的 "stale" 部分）
    var cachedHome = cache_1.getCached(cache_1.CACHE_KEYS.HOME_DATA);
    if (cachedHome && isLoggedIn) {
      this._applyHomeData(cachedHome, cachedProfile);
    }

    var tabBar = this.getTabBar();
    if (tabBar) tabBar.setData({ active: 0 });

    if (isLoggedIn) {
      this.loadHomeData();
    }
  },

  onPullDownRefresh: function () {
    var self = this;
    if (this.data.isLoggedIn) {
      this.loadHomeData(true)
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

  /**
   * ★ 优化：合并 checkOnboarding + loadHomeData
   * - 使用 swr 缓存，避免重复请求
   * - profile 只请求一次，同时用于 onboarding 检查和数据展示
   * - forceRefresh 用于下拉刷新
   */
  loadHomeData: function (forceReload) {
    var self = this;

    // 选择 swr（带缓存）还是 forceRefresh（强制刷新）
    var fetchProfile = forceReload
      ? cache_1.forceRefresh(cache_1.CACHE_KEYS.PROFILE, function () { return api_1.profileApi.get(); })
      : cache_1.swr(cache_1.CACHE_KEYS.PROFILE, function () { return api_1.profileApi.get(); },
          function (freshProfile) { self._applyProfileData(freshProfile); });

    var fetchHome = forceReload
      ? cache_1.forceRefresh(cache_1.CACHE_KEYS.HOME_DATA, function () { return api_1.homeApi.getData(); })
      : cache_1.swr(cache_1.CACHE_KEYS.HOME_DATA, function () { return api_1.homeApi.getData(); },
          function (freshHome) {
            var profile = cache_1.getCached(cache_1.CACHE_KEYS.PROFILE);
            self._applyHomeData(freshHome, profile);
          });

    return Promise.all([fetchHome, fetchProfile]).then(function (results) {
      var homeData = results[0] || {};
      var profileData = results[1] || {};

      // ★ onboarding 检查（复用同一个 profile 请求）
      if (profileData && !profileData.onboarding_completed) {
        wx.redirectTo({ url: '/pages/onboarding/onboarding' });
        return;
      }

      self._applyHomeData(homeData, profileData);
    }).catch(function (err) {
      console.error('loadHomeData error:', err);
    });
  },

  /**
   * ★ 新增：将 profile 数据应用到页面
   */
  _applyProfileData: function (profileData) {
    if (!profileData) return;
    var profile = {
      nickname: profileData.real_name || profileData.nickname || (wx.getStorageSync('user') || {}).nickname || '',
      age: profileData.age,
      tags: [],
    };
    if (profileData.blood_type) profile.tags.push(profileData.blood_type + '型血');
    if (profileData.allergies && profileData.allergies.length) {
      profile.tags.push('过敏: ' + profileData.allergies.join('、'));
    }
    this.setData({
      profile: profile,
      avatarText: profile.nickname ? profile.nickname.slice(0, 1) : '👤',
    });
  },

  /**
   * ★ 新增：将 home + profile 数据应用到页面
   */
  _applyHomeData: function (homeData, profileData) {
    if (!homeData) return;
    var self = this;

    var profile = {
      nickname: '',
      age: null,
      tags: [],
    };

    if (profileData) {
      profile.nickname = profileData.real_name || profileData.nickname || (wx.getStorageSync('user') || {}).nickname || '';
      profile.age = profileData.age;
      if (profileData.blood_type) profile.tags.push(profileData.blood_type + '型血');
      if (profileData.allergies && profileData.allergies.length) {
        profile.tags.push('过敏: ' + profileData.allergies.join('、'));
      }
    }

    var recentActivity = (homeData.recent_activity || []).map(function (r) {
      return {
        id: r.id,
        category: r.category,
        title: r.title || '未命名',
        date: r.record_date ? self._formatLocalDate(r.record_date) : (r.date || ''),
        ai_status: r.ai_status,
      };
    });

    // ★ 处理 dashboard 仪表盘数据
    var dash = homeData.dashboard || {};
    var dashAlerts = dash.alerts || [];
    var dashIndicators = dash.health_indicators || [];
    var dashMedAdherence = dash.med_adherence || null;
    var dashNutrition = null;

    if (dash.nutrition_7d) {
      var n = dash.nutrition_7d;
      var totalG = (n.protein || 0) + (n.fat || 0) + (n.carb || 0);
      var conicGradient = '';
      if (totalG > 0) {
        var pPct = Math.round(n.protein / totalG * 100);
        var fPct = Math.round(n.fat / totalG * 100);
        var cPct = 100 - pPct - fPct;
        conicGradient = '#2D8B6F 0% ' + pPct + '%, #F5A623 ' + pPct + '% ' + (pPct + fPct) + '%, #E85D3A ' + (pPct + fPct) + '% 100%';
      }
      var avgCal = n.days ? Math.round(n.total_calories / n.days) : 0;
      dashNutrition = {
        protein: n.protein || 0,
        fat: n.fat || 0,
        carb: n.carb || 0,
        totalCalories: n.total_calories || 0,
        avgCalories: avgCal,
        days: n.days || 0,
        conicGradient: conicGradient,
      };
    }

    self.setData({
      profile: profile,
      avatarText: profile.nickname ? profile.nickname.slice(0, 1) : '👤',
      pendingTasks: (homeData.pending_tasks || []).map(function (t) {
        return Object.assign({}, t, { completing: false });
      }),
      aiTip: homeData.ai_tip || '',
      recentActivity: recentActivity.slice(0, 5),
      alertCount: homeData.alert_count || 0,
      medSuggestions: (homeData.medication_suggestions || []).map(function (s) {
        return Object.assign({}, s, {
          name: s.name || '',
          dosage: s.dosage || '',
          frequency: s.frequency || '',
        });
      }),
      dashAlerts: dashAlerts,
      dashIndicators: dashIndicators,
      dashMedAdherence: dashMedAdherence,
      dashNutrition: dashNutrition,
    });
  },

  // ★ Fix: "YYYY-MM-DD" 会被 new Date() 当作 UTC 解析，导致时区偏移
  //   手动拆分为本地日期，避免跨时区日期错位
  _formatLocalDate: function (dateStr) {
    if (!dateStr) return '';
    var d;
    var s = dateStr.slice(0, 10);
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
      var parts = s.split('-');
      d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
    } else {
      d = new Date(dateStr);
    }
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
        // ★ 上传成功后失效相关缓存
        cache_1.invalidation.onRecordChange();
        (0, upload_1.pollBatchAIStatus)(result.recordIds, function () { self.loadHomeData(true); });
      })
      .catch(function () {});
  },

  onFileUpload: function () {
    console.log('[onFileUpload] called, chooseAndUploadFile =', typeof upload_1.chooseAndUploadFile);
    var self = this;
    if (!this._requireLogin()) return;
    if (typeof upload_1.chooseAndUploadFile !== 'function') {
      wx.showToast({ title: 'chooseAndUploadFile 未定义，请重新编译', icon: 'none' });
      return;
    }
    (0, upload_1.chooseAndUploadFile)({ maxCount: 5 })
        .then(function (result) {
          // ★ 上传成功后失效相关缓存
          cache_1.invalidation.onRecordChange();
          (0, upload_1.pollBatchAIStatus)(result.recordIds, function () { self.loadHomeData(true); });
        })
        .catch(function (err) { console.error('[onFileUpload] error:', err); });
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
      // ★ 打卡后失效 home 缓存
      cache_1.invalidation.onMedicationChange();
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

  // ★ 打开药物确认弹窗（收集详细信息）
  onConfirmSuggestion: function (e) {
    var id = e.currentTarget.dataset.id;
    if (!id) return;
    var name = '该药物';
    var dosage = '';
    var suggestions = this.data.medSuggestions || [];
    for (var i = 0; i < suggestions.length; i++) {
      if (suggestions[i].id === id) {
        name = suggestions[i].name;
        dosage = suggestions[i].dosage || '';
        break;
      }
    }
    this.setData({
      showMedConfirm: true,
      medConfirmId: id,
      medConfirmName: name,
      medConfirmDosage: dosage,
      medConfirmType: 'long_term',
      medConfirmTimes: 1,
      medConfirmDays: '',
      medConfirmSubmitting: false,
    });
  },

  hideMedConfirm: function () {
    this.setData({ showMedConfirm: false });
  },

  onMedConfirmDosage: function (e) {
    this.setData({ medConfirmDosage: e.detail.value });
  },

  onMedConfirmType: function (e) {
    this.setData({ medConfirmType: e.currentTarget.dataset.val });
  },

  onMedConfirmTimes: function (e) {
    this.setData({ medConfirmTimes: e.currentTarget.dataset.val });
  },

  onMedConfirmDays: function (e) {
    this.setData({ medConfirmDays: e.detail.value });
  },

  onSubmitMedConfirm: function () {
    var self = this;
    var id = this.data.medConfirmId;
    var medType = this.data.medConfirmType;
    var timesPerDay = this.data.medConfirmTimes;
    var dosage = this.data.medConfirmDosage.trim() || null;
    var totalDays = null;

    if ((medType === 'course' || medType === 'temporary') && !this.data.medConfirmDays) {
      wx.showToast({ title: '请输入服用天数', icon: 'none' });
      return;
    }
    if (medType === 'course' || medType === 'temporary') {
      totalDays = parseInt(this.data.medConfirmDays) || null;
    }

    this.setData({ medConfirmSubmitting: true });
    api_1.medsApi.confirmSuggestion(id, {
      med_type: medType,
      times_per_day: timesPerDay,
      dosage: dosage,
      total_days: totalDays,
    }).then(function () {
      var remaining = [];
      for (var j = 0; j < self.data.medSuggestions.length; j++) {
        if (self.data.medSuggestions[j].id !== id) remaining.push(self.data.medSuggestions[j]);
      }
      self.setData({ medSuggestions: remaining, showMedConfirm: false, medConfirmSubmitting: false });
      cache_1.invalidation.onMedicationChange();
      wx.showToast({ title: '已添加', icon: 'success' });
    }).catch(function () {
      self.setData({ medConfirmSubmitting: false });
      wx.showToast({ title: '添加失败', icon: 'none' });
    });
  },

  onDismissSuggestion: function (e) {
    var self = this;
    var id = e.currentTarget.dataset.id;
    if (!id) return;
    api_1.medsApi.dismissSuggestion(id).then(function () {
      var remaining = [];
      for (var j = 0; j < self.data.medSuggestions.length; j++) {
        if (self.data.medSuggestions[j].id !== id) remaining.push(self.data.medSuggestions[j]);
      }
      self.setData({ medSuggestions: remaining });
      cache_1.invalidation.onMedicationChange();
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
    clearTimeout(this._startSafetyTimer);
    clearTimeout(this._stopSafetyTimer);
    clearTimeout(this._stopFallbackTimer);
    clearInterval(this._recordTimer);
    this._recorderBusy = false;
    this._pendingStop = false;
    this.setData({ showVoiceModal: false, isRecording: false, recordingDuration: 0 });
  },

  noop: function () {},

  onRemoveSegment: function (e) {
    var index = e.currentTarget.dataset.index;
    var segs = this.data.voiceSegments.slice();
    segs.splice(index, 1);
    this.setData({ voiceSegments: segs });
  },

  onRecordStart: function () {
    if (this.data.isRecording || this._recorderBusy) return;
    this._pendingStop = false;
    this._startRecording();
  },

  onRecordEnd: function () {
    if (this.data.isRecording) {
      this._recorderBusy = true;
      this._stopRecording();

      var self = this;
      this._stopSafetyTimer = setTimeout(function () {
        if (self._recorderBusy) {
          console.warn('[Home Voice] stop safety timeout — force unlock');
          self._recorderBusy = false;
          self.setData({ isRecording: false, recordingDuration: 0 });
        }
      }, 3000);
    } else {
      this._pendingStop = true;
    }
  },

  _initRecorder: function () {
    if (this._recorder) return;
    var self = this;
    var recorder = wx.getRecorderManager();

    recorder.onStart(function () {
      clearTimeout(self._startSafetyTimer);
      self._recorderBusy = false;
      self._recordStartTime = Date.now();
      self.setData({ isRecording: true, recordingDuration: 0 });
      self._recordTimer = setInterval(function () {
        self.setData({ recordingDuration: self.data.recordingDuration + 1 });
      }, 1000);

      if (self._pendingStop) {
        self._pendingStop = false;
        self._recorder.stop();
      }
    });

    recorder.onStop(function (res) {
      clearInterval(self._recordTimer);
      clearTimeout(self._stopFallbackTimer);
      clearTimeout(self._stopSafetyTimer);
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
      clearTimeout(self._startSafetyTimer);
      clearTimeout(self._stopSafetyTimer);
      self._recorderBusy = false;
      self._pendingStop = false;
      self.setData({ isRecording: false, recordingDuration: 0 });
      wx.showToast({ title: '录音失败，请重试', icon: 'none' });
    });

    self._recorder = recorder;
  },

  _startRecording: function () {
    var self = this;
    this._initRecorder();
    wx.getSetting({
      success: function (res) {
        if (res.authSetting['scope.record']) {
          self._doStartRecording();
        } else {
          wx.authorize({
            scope: 'scope.record',
            success: function () {
              wx.showToast({ title: '权限已获取，请再次按住说话', icon: 'none' });
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
        }
      },
    });
  },

  _doStartRecording: function () {
    var self = this;
    self._recorderBusy = true;
    self._recorder.start({
      format: 'mp3',
      sampleRate: 16000,
      numberOfChannels: 1,
      encodeBitRate: 48000,
      duration: 60000,
    });

    self._startSafetyTimer = setTimeout(function () {
      if (self._recorderBusy && !self.data.isRecording) {
        console.warn('[Home Voice] start safety timeout — force unlock');
        self._recorderBusy = false;
      }
    }, 3000);

    self._stopFallbackTimer = setTimeout(function () {
      if (self.data.isRecording) self._stopRecording();
    }, 55000);
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
        api_1.medsApi.voiceAddAudio(audioKeys).then(function (res) {
          var items = (res && res.items) || [];
          var typeIcons = { medication: '💊', food: '🍽️', vitals: '❤️', symptom: '📝', insurance: '🛡️', memo: '📋' };
          if (items.length > 0) {
            var labels = items.map(function (i) { return (typeIcons[i.type] || '✅') + (i.summary || ''); }).join(' ');
            wx.showToast({ title: labels.slice(0, 40) || '✅ 记录成功', icon: 'none', duration: 2500 });
          } else {
            wx.showToast({ title: '✅ 记录成功', icon: 'none' });
          }
          // ★ 语音记录成功后失效缓存
          cache_1.invalidation.onRecordChange();
          self.loadHomeData(true);
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

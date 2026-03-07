"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
var api_1 = require("../../services/api");
var upload_1 = require("../../services/upload");

var PLACEHOLDERS = [
  '今天感觉怎么样？随时聊聊~',
  '有什么健康问题想了解的吗？',
  '我在这里，有什么需要帮忙的~',
  '想聊聊最近的身体状况吗？',
  '药吃了吗？有什么想问的尽管说~',
  '最近睡得好吗？跟我聊聊吧~',
  '有什么不舒服的地方吗？',
  '记录一下今天的健康状况吧~',
  '需要查看最近的健康数据吗？',
  '今天过得怎么样？我来帮你看看~',
];

Page({
  data: {
    isLoggedIn: false,
    messages: [],
    inputText: '',
    typing: false,
    sessionId: '',
    scrollToView: '',
    placeholder: '今天感觉怎么样？随时聊聊~',

    inputMode: 'voice',
    isRecording: false,
    recordingDuration: 0,

    homePrompts: [
      { icon: '🍽️', text: '过去7天饮食情况' },
      { icon: '💊', text: '这周药吃齐了吗' },
      { icon: '💉', text: '血压最近趋势怎样' },
      { icon: '📋', text: '最近身体怎么样' },
      { icon: '📈', text: 'PSA 变化趋势' },
      { icon: '🛡️', text: '保险什么时候到期' },
      { icon: '⚠️', text: '有什么需要注意的' },
      { icon: '🏥', text: '下次该做什么检查' },
    ],

    followupPrompts: [
      { icon: '📈', text: 'PSA 变化趋势' },
      { icon: '🏥', text: '下次该做什么检查' },
      { icon: '🛡️', text: '保险什么时候到期' },
      { icon: '💊', text: '用药依从性怎么样' },
      { icon: '🍽️', text: '最近营养均衡吗' },
    ],
  },

  _streamText: '',
  _streamMsgIdx: -1,
  _streamTask: null,
  _throttleTimer: null,
  _typingTimer: null,
  _recorder: null,
  _recordTimer: null,
  _stopFallbackTimer: null,
  _recordStartTime: 0,
  _pendingStop: false,
  _recorderBusy: false,
  _startSafetyTimer: null,   // ★ 新增：recorder.start 安全超时
  _stopSafetyTimer: null,    // ★ 新增：recorder.stop 安全超时

  onShow: function () {
    var tabBar = this.getTabBar();
    if (tabBar) tabBar.setData({ active: 2 });

    var token = wx.getStorageSync('token');
    this.setData({ isLoggedIn: !!token });

    var idx = Math.floor(Math.random() * PLACEHOLDERS.length);
    this.setData({ placeholder: PLACEHOLDERS[idx] });

    var app = getApp();
    var initQ = app.globalData && app.globalData.chatInitQuestion;
    if (initQ) {
      app.globalData.chatInitQuestion = '';
      var self = this;
      setTimeout(function () { self.sendMessage(initQ); }, 200);
    }

    // ★ [Fix-1] 如果从后台回来，流式请求已被系统断开，检测并提示
    if (this.data.typing && !this._streamTask) {
      this.setData({ typing: false });
    }
  },

  // ★ [Fix-1] 不再 abort 流式请求，只停录音
  // 原问题：切换页面时 abort 导致 AI 回答中断
  onHide: function () {
    // 只停止录音（后台录音无意义）
    if (this.data.isRecording) {
      if (this._recorder) this._recorder.stop();
      clearInterval(this._recordTimer);
      clearTimeout(this._stopFallbackTimer);
      clearTimeout(this._startSafetyTimer);
      clearTimeout(this._stopSafetyTimer);
      this._recorderBusy = false;
      this.setData({ isRecording: false, recordingDuration: 0 });
    }
    // ★ 不再 abort _streamTask，让流式请求在后台继续完成
  },

  _requireLogin: function () {
    if (!this.data.isLoggedIn) {
      wx.showModal({
        title: '需要登录',
        content: '请先登录后再使用AI助手',
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

  _clearTimers: function () {
    if (this._throttleTimer) { clearTimeout(this._throttleTimer); this._throttleTimer = null; }
    if (this._typingTimer) { clearTimeout(this._typingTimer); this._typingTimer = null; }
  },

  onInputChange: function (e) {
    this.setData({ inputText: e.detail.value });
  },

  onInputConfirm: function () {
    this.sendMessage(this.data.inputText);
  },

  onPromptTap: function (e) {
    this.sendMessage(e.currentTarget.dataset.text);
  },

  // ══════════════════════════════
  //  语音/文字切换
  // ══════════════════════════════

  onToggleInputMode: function () {
    this.setData({ inputMode: this.data.inputMode === 'voice' ? 'text' : 'voice' });
  },

  // ══════════════════════════════
  //  语音录音
  // ══════════════════════════════

  _initChatRecorder: function () {
    if (this._recorder) return;
    var self = this;
    var recorder = wx.getRecorderManager();

    recorder.onStart(function () {
      clearTimeout(self._startSafetyTimer);  // ★ 清除安全超时
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
      clearTimeout(self._stopSafetyTimer);  // ★ 清除 stop 安全超时
      self._recorderBusy = false;
      var duration = Math.round((Date.now() - self._recordStartTime) / 1000);
      self.setData({ isRecording: false, recordingDuration: 0 });

      if (duration >= 1 && res.tempFilePath) {
        self._sendVoiceMessage(res.tempFilePath);
      } else if (duration < 1) {
        wx.showToast({ title: '录音太短', icon: 'none' });
      }
    });

    recorder.onError(function (err) {
      console.error('[Chat Voice] recorder error:', err);
      clearInterval(self._recordTimer);
      clearTimeout(self._stopFallbackTimer);
      clearTimeout(self._startSafetyTimer);
      clearTimeout(self._stopSafetyTimer);
      self._recorderBusy = false;
      self.setData({ isRecording: false, recordingDuration: 0 });
      wx.showToast({ title: '录音失败，请重试', icon: 'none' });
    });

    self._recorder = recorder;
  },

  onVoiceRecordStart: function () {
    if (this.data.isRecording || this.data.typing || this._recorderBusy) return;
    if (!this._requireLogin()) return;
    this._pendingStop = false;
    this._startChatRecording();
  },

  // ★ [Fix-2] 松开结束录音 — 增加安全超时防止永久锁死
  onVoiceRecordEnd: function () {
    if (this.data.isRecording) {
      this._recorderBusy = true;
      clearTimeout(this._stopFallbackTimer);
      if (this._recorder) this._recorder.stop();

      // ★ 安全超时：3秒后如果 onStop 还没回调，强制解锁
      var self = this;
      this._stopSafetyTimer = setTimeout(function () {
        if (self._recorderBusy) {
          console.warn('[Chat Voice] stop safety timeout — force unlock');
          self._recorderBusy = false;
          self.setData({ isRecording: false, recordingDuration: 0 });
        }
      }, 3000);
    } else {
      this._pendingStop = true;
    }
  },

  _startChatRecording: function () {
    var self = this;
    this._initChatRecorder();
    wx.getSetting({
      success: function (res) {
        if (res.authSetting['scope.record']) {
          self._doStartChatRecording();
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

  _doStartChatRecording: function () {
    var self = this;
    self._recorderBusy = true;
    self._recorder.start({
      format: 'mp3',
      sampleRate: 16000,
      numberOfChannels: 1,
      encodeBitRate: 48000,
      duration: 60000,
    });

    // ★ 安全超时：3秒后如果 onStart 还没回调，解锁 busy
    self._startSafetyTimer = setTimeout(function () {
      if (self._recorderBusy && !self.data.isRecording) {
        console.warn('[Chat Voice] start safety timeout — force unlock');
        self._recorderBusy = false;
      }
    }, 3000);

    self._stopFallbackTimer = setTimeout(function () {
      if (self.data.isRecording && self._recorder) self._recorder.stop();
    }, 55000);
  },

  _sendVoiceMessage: function (tempFilePath) {
    var self = this;
    var userMsg = { id: 'msg_' + Date.now(), role: 'user', text: '🎙️ 语音提问', isVoice: true };
    var messages = this.data.messages.slice();
    messages.push(userMsg);
    var userIdx = messages.length - 1;

    var aiMsg = { id: 'ai_' + Date.now(), role: 'assistant', text: '', charts: [] };
    messages.push(aiMsg);
    var aiIdx = messages.length - 1;

    this._streamText = '';
    this._streamMsgIdx = aiIdx;
    this._clearTimers();
    this.setData({ messages: messages, typing: true, scrollToView: 'msg-' + aiIdx });

    (0, upload_1.uploadAudioToCOS)(tempFilePath).then(function (result) {
      self._streamTask = api_1.chatApi.streamVoice(
          {
            audio_keys: [result.fileKey],
            session_id: self.data.sessionId || undefined,
            include_family: false,
          },
          {
            // ★ [Fix-4] 收到转录文字 → 更新用户气泡（替换"语音提问"）
            onTranscript: function (text) {
              if (text) {
                self.setData({ ['messages[' + userIdx + '].text']: text });
              }
            },
            onCharts: function (charts) {
              self.setData({ ['messages[' + aiIdx + '].charts']: self._processCharts(charts) });
              self._scrollToBottom();
            },
            onText: function (delta) {
              self._streamText += delta;
              self._throttledUpdateText();
            },
            onDone: function (sessionId) {
              self._clearTimers();
              self._streamTask = null;  // ★ 清除引用
              if (self._streamText) {
                self.setData({
                  ['messages[' + aiIdx + '].text']: self._streamText,
                  typing: false,
                  sessionId: sessionId || self.data.sessionId,
                });
              } else {
                self.setData({ typing: false });
              }
              self._scrollToBottom();
            },
            onError: function (err) {
              self._clearTimers();
              self._streamTask = null;  // ★ 清除引用
              self.setData({
                ['messages[' + aiIdx + '].text']: self._streamText || '抱歉，语音识别出错了，请重试',
                typing: false,
              });
            },
            // ★ [Fix-4] 修正回调名：onFallback → onFallbackComplete（与 api.ts 接口匹配）
            // api.ts 的 success 兜底检查的是 callbacks.onFallbackComplete
            // 旧代码用 onFallback 导致走 _parseAllSSELines，transcript 虽能处理但缺少逐字动画
            onFallbackComplete: function (fullText, charts, sessionId) {
              self._clearTimers();
              self._streamTask = null;
              // ★ fallback 模式下也要处理 transcript（从 _collectSSEData 无法提取）
              // 但 fullText 已经是完整的 AI 回答文字
              if (charts && charts.length > 0) {
                self.setData({ ['messages[' + aiIdx + '].charts']: self._processCharts(charts) });
              }
              self._simulateTyping(fullText, aiIdx, sessionId);
            },
          },
      );
    }).catch(function (err) {
      self._streamTask = null;
      self.setData({ ['messages[' + aiIdx + '].text']: '语音上传失败，请重试', typing: false });
    });
  },

  // ══════════════════════════════
  //  文字发送
  // ══════════════════════════════

  sendMessage: function (text) {
    if (!text || !text.trim() || this.data.typing) return;
    if (!this._requireLogin()) return;
    var self = this;
    var question = text.trim();
    var userMsg = { id: 'msg_' + Date.now(), role: 'user', text: question };
    var messages = this.data.messages.slice();
    messages.push(userMsg);

    var aiMsg = { id: 'ai_' + Date.now(), role: 'assistant', text: '', charts: [] };
    messages.push(aiMsg);
    var aiIdx = messages.length - 1;

    this._streamText = '';
    this._streamMsgIdx = aiIdx;
    this._clearTimers();
    this.setData({ messages: messages, inputText: '', typing: true, scrollToView: 'msg-' + aiIdx });

    this._streamTask = api_1.chatApi.stream(
      { question: question, session_id: this.data.sessionId || undefined, include_family: false },
      {
        onCharts: function (charts) {
          self.setData({ ['messages[' + aiIdx + '].charts']: self._processCharts(charts) });
          self._scrollToBottom();
        },
        onText: function (delta) {
          self._streamText += delta;
          self._throttledUpdateText();
        },
        onDone: function (sessionId) {
          self._clearTimers();
          self._streamTask = null;  // ★ 清除引用
          if (self._streamText) {
            self.setData({
              ['messages[' + aiIdx + '].text']: self._streamText,
              typing: false,
              sessionId: sessionId || self.data.sessionId,
            });
          } else {
            self.setData({ typing: false });
          }
          self._scrollToBottom();
        },
        onError: function (err) {
          self._clearTimers();
          self._streamTask = null;  // ★ 清除引用
          self.setData({
            ['messages[' + aiIdx + '].text']: self._streamText || '抱歉，请求出错了，请稍后再试',
            typing: false,
          });
        },
        // ★ [Fix-4] 统一使用 onFallbackComplete（3 个参数）
        onFallbackComplete: function (fullText, charts, sessionId) {
          self._clearTimers();
          self._streamTask = null;
          if (charts && charts.length > 0) {
            self.setData({ ['messages[' + aiIdx + '].charts']: self._processCharts(charts) });
          }
          self._simulateTyping(fullText, aiIdx, sessionId);
        },
      },
    );
  },

  _simulateTyping: function (fullText, aiIdx, sessionId) {
    var self = this;
    var i = 0;
    var step = function () {
      if (i >= fullText.length) {
        self.setData({
          ['messages[' + aiIdx + '].text']: fullText,
          typing: false,
          sessionId: sessionId || self.data.sessionId,
        });
        self._scrollToBottom();
        return;
      }
      i += Math.min(3, fullText.length - i);
      self.setData({ ['messages[' + aiIdx + '].text']: fullText.slice(0, i) });
      self._typingTimer = setTimeout(step, 30);
    };
    step();
  },

  _throttledUpdateText: function () {
    var self = this;
    if (this._throttleTimer) return;
    this._throttleTimer = setTimeout(function () {
      self._throttleTimer = null;
      var idx = self._streamMsgIdx;
      if (idx >= 0 && self._streamText) {
        self.setData({ ['messages[' + idx + '].text']: self._streamText });
        self._scrollToBottom();
      }
    }, 80);
  },

  _scrollToBottom: function () {
    var self = this;
    setTimeout(function () { self.setData({ scrollToView: 'scroll-bottom' }); }, 50);
  },

  _processCharts: function (charts) {
    return (charts || []).map(function (chart) {

      // ── adherence → med_adherence (type mapping + data restructure) ──
      if (chart.type === 'adherence') {
        chart.type = 'med_adherence';
        if (Array.isArray(chart.medications)) {
          chart.data = chart.medications.map(function (m) {
            var pct = m.total > 0 ? Math.round(m.done / m.total * 100) : 0;
            return { name: m.name, pct: pct, done: m.done, total: m.total };
          });
        }
        if (chart.summary && typeof chart.summary === 'object' && !chart.summary.text) {
          chart.summary = {
            text: '总体依从率 ' + (chart.summary.rate || 0) + '%，已完成 ' + (chart.summary.done || 0) + '/' + (chart.summary.total || 0) + ' 次'
          };
        }
      }

      // ── dual_line → pre-compute val1/val2/pct1/pct2 for WXML ──
      if (chart.type === 'dual_line' && Array.isArray(chart.data)) {
        var key1 = chart.key1 || '';
        var key2 = chart.key2 || '';
        chart.key1Name = key1;
        chart.key2Name = key2;
        var maxV = 0;
        for (var k = 0; k < chart.data.length; k++) {
          var v1 = chart.data[k][key1] || 0;
          var v2 = chart.data[k][key2] || 0;
          if (v1 > maxV) maxV = v1;
          if (v2 > maxV) maxV = v2;
        }
        chart.data = chart.data.map(function (d) {
          return {
            label: d.label,
            val1: d[key1] || 0,
            val2: d[key2] || 0,
            pct1: maxV > 0 ? Math.round((d[key1] || 0) / maxV * 100) : 50,
            pct2: maxV > 0 ? Math.round((d[key2] || 0) / maxV * 100) : 50,
          };
        });
        if (chart.summary && typeof chart.summary === 'object' && !chart.summary.text) {
          chart.summary = {
            text: '最近血压 ' + (chart.summary.latest || '') + ' mmHg，共 ' + (chart.summary.count || 0) + ' 次记录'
          };
        }
      }

      // ── line → add pct for horizontal bar width ──
      if (chart.type === 'line' && Array.isArray(chart.data)) {
        var lineMax = 0;
        for (var li = 0; li < chart.data.length; li++) {
          if ((chart.data[li].value || 0) > lineMax) lineMax = chart.data[li].value;
        }
        chart.data = chart.data.map(function (d) {
          return Object.assign({}, d, {
            pct: lineMax > 0 ? Math.round(d.value / lineMax * 100) : 50
          });
        });
        if (chart.summary && typeof chart.summary === 'object' && !chart.summary.text) {
          var lineParts = [];
          if (chart.summary.latest !== undefined) lineParts.push('最新值 ' + chart.summary.latest + (chart.unit || ''));
          if (chart.summary.change_pct !== undefined) {
            var dir = chart.summary.change_pct >= 0 ? '↑' : '↓';
            lineParts.push(dir + ' ' + Math.abs(chart.summary.change_pct) + '%');
          }
          chart.summary = { text: lineParts.join('，') };
        }
      }

      // ── pie: compute conic-gradient for donut ring visualization ──
      if (chart.type === 'pie' && Array.isArray(chart.data)) {
        var total = chart.data.reduce(function (s, d) { return s + (d.value || 0); }, 0);
        var cumPct = 0;
        chart.data = chart.data.map(function (d) {
          var pct = total > 0 ? Math.round((d.value / total) * 100) : 0;
          var startPct = cumPct;
          cumPct += pct;
          return Object.assign({}, d, { pct: pct, startPct: startPct, endPct: cumPct });
        });
        if (chart.data.length > 0) {
          chart.data[chart.data.length - 1].endPct = 100;
        }
        var parts = [];
        for (var i = 0; i < chart.data.length; i++) {
          var dd = chart.data[i];
          parts.push((dd.color || '#ccc') + ' ' + dd.startPct + '% ' + dd.endPct + '%');
        }
        chart.conicGradient = parts.join(', ');
        chart.total = total;
        if (chart.summary && typeof chart.summary === 'object' && !chart.summary.text) {
          var pieParts = [];
          if (chart.summary.total_calories) pieParts.push('总热量 ' + chart.summary.total_calories + 'kcal');
          if (chart.summary.avg_calories) pieParts.push('日均 ' + chart.summary.avg_calories + 'kcal');
          if (chart.summary.days) pieParts.push('共 ' + chart.summary.days + ' 天');
          chart.summary = { text: pieParts.join('，') };
        }
      }

      // ── donut: compute conic-gradient ──
      if (chart.type === 'donut' && Array.isArray(chart.data)) {
        var defColors = ['#4DB892', '#5B9BD5', '#F5A623', '#E55B5B', '#9B59B6'];
        var colors = chart.colors || defColors;
        var total2 = chart.data.reduce(function (s, d) { return s + (d.value || 0); }, 0);
        var cumPct2 = 0;
        chart.data = chart.data.map(function (d, idx) {
          var pct = d.pct || (total2 > 0 ? Math.round((d.value / total2) * 100) : 0);
          var startPct = cumPct2;
          cumPct2 += pct;
          return Object.assign({}, d, {
            pct: pct, startPct: startPct, endPct: cumPct2,
            color: colors[idx % colors.length]
          });
        });
        if (chart.data.length > 0) {
          chart.data[chart.data.length - 1].endPct = 100;
        }
        var parts2 = [];
        for (var j = 0; j < chart.data.length; j++) {
          var dd2 = chart.data[j];
          parts2.push(dd2.color + ' ' + dd2.startPct + '% ' + dd2.endPct + '%');
        }
        chart.conicGradient = parts2.join(', ');
        chart.total = total2;
      }

      // ── Fallback: convert any remaining object summary to {text: ''} ──
      if (chart.summary && typeof chart.summary === 'object' && !chart.summary.text) {
        chart.summary = { text: '' };
      }

      return chart;
    });
  },

  onNewChat: function () {
    if (this._streamTask && this._streamTask.abort) this._streamTask.abort();
    this._streamTask = null;
    this._clearTimers();
    this.setData({ messages: [], sessionId: '', typing: false, inputText: '' });
  },
});

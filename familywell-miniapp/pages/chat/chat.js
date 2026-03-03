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
  _recorderBusy: false,   // ★ 防止 stop 未完成就再 start

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
  },

  onHide: function () {
    if (this._streamTask && this._streamTask.abort) this._streamTask.abort();
    this._clearTimers();
    if (this.data.isRecording) {
      if (this._recorder) this._recorder.stop();
      clearInterval(this._recordTimer);
      this.setData({ isRecording: false });
    }
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
      self._recorderBusy = false;  // ★ start 成功，不再 busy
      self._recordStartTime = Date.now();
      self.setData({ isRecording: true, recordingDuration: 0 });
      self._recordTimer = setInterval(function () {
        self.setData({ recordingDuration: self.data.recordingDuration + 1 });
      }, 1000);

      // ★ 用户在 onStart 前就松手了 → 立即停止（不延迟）
      if (self._pendingStop) {
        self._pendingStop = false;
        // 立即 stop，onStop 里 duration<1s 会自动丢弃
        self._recorder.stop();
      }
    });

    recorder.onStop(function (res) {
      clearInterval(self._recordTimer);
      clearTimeout(self._stopFallbackTimer);
      self._recorderBusy = false;  // ★ stop 完成，可以再次录音
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
      self._recorderBusy = false;  // ★ 出错也要解锁
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

  onVoiceRecordEnd: function () {
    if (this.data.isRecording) {
      this._recorderBusy = true;  // ★ stop 也是异步的，锁住直到 onStop
      clearTimeout(this._stopFallbackTimer);
      if (this._recorder) this._recorder.stop();
    } else {
      this._pendingStop = true;
    }
  },

  _startChatRecording: function () {
    var self = this;
    this._initChatRecorder();
    wx.authorize({
      scope: 'scope.record',
      success: function () {
        self._recorderBusy = true;  // ★ 标记为忙，直到 onStart/onStop/onError
        self._recorder.start({
          format: 'mp3',
          sampleRate: 16000,
          numberOfChannels: 1,
          encodeBitRate: 48000,
          duration: 60000,
        });
        self._stopFallbackTimer = setTimeout(function () {
          if (self.data.isRecording && self._recorder) self._recorder.stop();
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

  _sendVoiceMessage: function (tempFilePath) {
    var self = this;
    var userMsg = { id: 'msg_' + Date.now(), role: 'user', text: '🎙️ 语音提问', isVoice: true };
    var messages = this.data.messages.slice();
    messages.push(userMsg);

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
            self.setData({
              ['messages[' + aiIdx + '].text']: self._streamText || '抱歉，语音识别出错了，请重试',
              typing: false,
            });
          },
          onFallback: function (fullText, sessionId) {
            self._clearTimers();
            self._simulateTyping(fullText, aiIdx, sessionId);
          },
        },
      );
    }).catch(function (err) {
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
          self.setData({
            ['messages[' + aiIdx + '].text']: self._streamText || '抱歉，请求出错了，请稍后再试',
            typing: false,
          });
        },
        onFallback: function (fullText, sessionId) {
          self._clearTimers();
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
      if (chart.type === 'pie' && Array.isArray(chart.data)) {
        var total = chart.data.reduce(function (s, d) { return s + (d.value || 0); }, 0);
        chart.data = chart.data.map(function (d) {
          return Object.assign({}, d, { pct: total > 0 ? Math.round((d.value / total) * 100) : 0 });
        });
      }
      return chart;
    });
  },

  onNewChat: function () {
    if (this._streamTask && this._streamTask.abort) this._streamTask.abort();
    this._clearTimers();
    this.setData({ messages: [], sessionId: '', typing: false, inputText: '' });
  },
});

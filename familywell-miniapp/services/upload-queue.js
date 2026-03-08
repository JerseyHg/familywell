"use strict";
Object.defineProperty(exports, "__esModule", { value: true });

var api_1 = require("./api");
var cache_1 = require("./cache");

var STORAGE_KEY = 'upload_queue';
var MAX_RETRIES = 3;

function _genId() {
  return 'q_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
}

function getQueue() {
  try {
    return wx.getStorageSync(STORAGE_KEY) || [];
  } catch (_e) {
    return [];
  }
}
exports.getQueue = getQueue;

function _saveQueue(queue) {
  wx.setStorageSync(STORAGE_KEY, queue);
}

function addToQueue(item) {
  var queue = getQueue();
  queue.push(Object.assign({}, item, {
    id: _genId(),
    status: 'pending',
    retryCount: 0,
    createdAt: Date.now(),
  }));
  _saveQueue(queue);
  console.log('[UploadQueue] Item added, queue size:', queue.length);
}
exports.addToQueue = addToQueue;

function removeFromQueue(id) {
  var queue = getQueue().filter(function (item) { return item.id !== id; });
  _saveQueue(queue);
}
exports.removeFromQueue = removeFromQueue;

function clearQueue() {
  _saveQueue([]);
}
exports.clearQueue = clearQueue;

function getPendingCount() {
  return getQueue().filter(function (item) {
    return item.status !== 'failed' || item.retryCount < MAX_RETRIES;
  }).length;
}
exports.getPendingCount = getPendingCount;

function _uploadToCOS(filePath, uploadUrl, contentType) {
  return new Promise(function (resolve, reject) {
    var fs = wx.getFileSystemManager();
    fs.readFile({
      filePath: filePath,
      success: function (res) {
        wx.request({
          url: uploadUrl,
          method: 'PUT',
          header: { 'Content-Type': contentType },
          data: res.data,
          success: function (resp) {
            if (resp.statusCode >= 200 && resp.statusCode < 300) {
              resolve();
            } else {
              reject(new Error('COS upload failed: ' + resp.statusCode));
            }
          },
          fail: reject,
        });
      },
      fail: reject,
    });
  });
}

function _processItem(item) {
  try {
    wx.getFileSystemManager().accessSync(item.tempFilePath);
  } catch (_e) {
    console.warn('[UploadQueue] Temp file no longer exists:', item.tempFilePath);
    return Promise.resolve(false);
  }

  return api_1.recordsApi.getUploadUrl({
    file_name: item.fileName,
    content_type: item.contentType,
  }).then(function (urlRes) {
    return _uploadToCOS(item.tempFilePath, urlRes.upload_url, item.contentType).then(function () {
      var createData = {
        file_key: urlRes.file_key,
        file_type: item.fileType,
        source: item.type === 'audio' ? 'voice' : 'camera',
      };
      if (item.projectId) {
        createData.project_id = item.projectId;
      }
      return api_1.recordsApi.create(createData);
    });
  }).then(function () {
    return true;
  }).catch(function (err) {
    console.error('[UploadQueue] Process item failed:', err);
    return false;
  });
}

var _processing = false;

function processQueue() {
  if (_processing) return Promise.resolve({ success: 0, failed: 0 });
  _processing = true;

  var queue = getQueue();
  var retryable = queue.filter(function (item) {
    return (item.status === 'pending' || item.status === 'failed') && item.retryCount < MAX_RETRIES;
  });

  if (retryable.length === 0) {
    _processing = false;
    return Promise.resolve({ success: 0, failed: 0 });
  }

  console.log('[UploadQueue] Processing', retryable.length, 'items');
  var success = 0;
  var failed = 0;

  var processNext = function (i) {
    if (i >= retryable.length) {
      if (success > 0) {
        cache_1.invalidation.onRecordChange();
        wx.showToast({
          title: success + ' 个文件重新上传成功',
          icon: 'none',
          duration: 2000,
        });
      }
      _processing = false;
      return Promise.resolve({ success: success, failed: failed });
    }

    var item = retryable[i];
    item.status = 'uploading';
    _saveQueue(queue);

    return _processItem(item).then(function (ok) {
      if (ok) {
        var idx = queue.indexOf(item);
        if (idx >= 0) queue.splice(idx, 1);
        _saveQueue(queue);
        success++;
      } else {
        item.retryCount++;
        item.status = item.retryCount >= MAX_RETRIES ? 'failed' : 'pending';
        item.error = '上传失败';
        _saveQueue(queue);
        failed++;
      }
      return processNext(i + 1);
    });
  };

  return processNext(0);
}
exports.processQueue = processQueue;

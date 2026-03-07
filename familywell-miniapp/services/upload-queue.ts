/**
 * services/upload-queue.ts — 离线上传队列
 * ═══════════════════════════════════════════
 * 上传失败时自动存入本地队列，恢复网络后自动重试。
 * 队列数据持久化到 wx.setStorage，最大重试 3 次。
 */

import { recordsApi } from './api'
import { invalidation } from './cache'

const STORAGE_KEY = 'upload_queue'
const MAX_RETRIES = 3

export interface QueuedItem {
  id: string
  type: 'image' | 'file' | 'audio'
  tempFilePath: string
  fileName: string
  contentType: string
  fileType: string       // 'image' | 'pdf' | 'audio'
  projectId?: number
  status: 'pending' | 'uploading' | 'failed'
  retryCount: number
  createdAt: number
  error?: string
}

/** 生成简单唯一 ID */
function _genId(): string {
  return 'q_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8)
}

/** 从本地存储读取队列 */
export function getQueue(): QueuedItem[] {
  try {
    return wx.getStorageSync(STORAGE_KEY) || []
  } catch {
    return []
  }
}

/** 保存队列到本地存储 */
function _saveQueue(queue: QueuedItem[]): void {
  wx.setStorageSync(STORAGE_KEY, queue)
}

/** 添加失败项到队列 */
export function addToQueue(item: Omit<QueuedItem, 'id' | 'status' | 'retryCount' | 'createdAt'>): void {
  const queue = getQueue()
  queue.push({
    ...item,
    id: _genId(),
    status: 'pending',
    retryCount: 0,
    createdAt: Date.now(),
  })
  _saveQueue(queue)
  console.log('[UploadQueue] Item added, queue size:', queue.length)
}

/** 从队列中移除指定项 */
export function removeFromQueue(id: string): void {
  const queue = getQueue().filter(item => item.id !== id)
  _saveQueue(queue)
}

/** 清空队列 */
export function clearQueue(): void {
  _saveQueue([])
}

/** 获取待处理的队列项数量 */
export function getPendingCount(): number {
  return getQueue().filter(item => item.status !== 'failed' || item.retryCount < MAX_RETRIES).length
}

/** 通用 COS 上传（与 upload.ts 中逻辑一致） */
async function _uploadToCOS(filePath: string, uploadUrl: string, contentType: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const fs = wx.getFileSystemManager()
    fs.readFile({
      filePath,
      success: (res) => {
        wx.request({
          url: uploadUrl,
          method: 'PUT',
          header: { 'Content-Type': contentType },
          data: res.data,
          success: (resp) => {
            if (resp.statusCode >= 200 && resp.statusCode < 300) {
              resolve()
            } else {
              reject(new Error(`COS upload failed: ${resp.statusCode}`))
            }
          },
          fail: reject,
        })
      },
      fail: reject,
    })
  })
}

/** 处理单个队列项 */
async function _processItem(item: QueuedItem): Promise<boolean> {
  try {
    // 检查临时文件是否还存在
    try {
      wx.getFileSystemManager().accessSync(item.tempFilePath)
    } catch {
      // 临时文件已被清理，无法重试
      console.warn('[UploadQueue] Temp file no longer exists:', item.tempFilePath)
      return false
    }

    // 1. 获取预签名 URL
    const urlRes: any = await recordsApi.getUploadUrl({
      file_name: item.fileName,
      content_type: item.contentType,
    })

    // 2. 上传到 COS
    await _uploadToCOS(item.tempFilePath, urlRes.upload_url, item.contentType)

    // 3. 创建记录
    const createData: any = {
      file_key: urlRes.file_key,
      file_type: item.fileType,
      source: item.type === 'audio' ? 'voice' : 'camera',
    }
    if (item.projectId) {
      createData.project_id = item.projectId
    }
    await recordsApi.create(createData)

    return true
  } catch (err) {
    console.error('[UploadQueue] Process item failed:', err)
    return false
  }
}

/** 处理整个队列，逐个重试 */
let _processing = false
export async function processQueue(): Promise<{ success: number; failed: number }> {
  if (_processing) return { success: 0, failed: 0 }
  _processing = true

  const queue = getQueue()
  const retryable = queue.filter(
    item => (item.status === 'pending' || item.status === 'failed') && item.retryCount < MAX_RETRIES
  )

  if (retryable.length === 0) {
    _processing = false
    return { success: 0, failed: 0 }
  }

  console.log('[UploadQueue] Processing', retryable.length, 'items')
  let success = 0
  let failed = 0

  for (const item of retryable) {
    // 更新状态为 uploading
    item.status = 'uploading'
    _saveQueue(queue)

    const ok = await _processItem(item)

    if (ok) {
      // 成功：从队列中移除
      const idx = queue.indexOf(item)
      if (idx >= 0) queue.splice(idx, 1)
      _saveQueue(queue)
      success++
    } else {
      // 失败：增加重试计数
      item.retryCount++
      item.status = item.retryCount >= MAX_RETRIES ? 'failed' : 'pending'
      item.error = '上传失败'
      _saveQueue(queue)
      failed++
    }
  }

  if (success > 0) {
    invalidation.onRecordChange()
    wx.showToast({
      title: `${success} 个文件重新上传成功`,
      icon: 'none',
      duration: 2000,
    })
  }

  _processing = false
  return { success, failed }
}

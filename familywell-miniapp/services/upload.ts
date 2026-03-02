/**
 * services/upload.ts — 文件上传服务
 * ═══════════════════════════════════════
 * ★ 新增 uploadAudioToCOS：上传音频文件到 COS
 */

import { recordsApi } from './api'

/**
 * Single upload flow (backward compatible):
 * Choose 1 image → upload → create record → return recordId
 */
export function chooseAndUpload(): Promise<{ recordId: number }> {
  return new Promise((resolve, reject) => {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      camera: 'back',
      success: async (res) => {
        const file = res.tempFiles[0]
        const filePath = file.tempFilePath
        const fileName = filePath.split('/').pop() || 'photo.jpg'

        wx.showLoading({ title: '上传中...', mask: true })

        try {
          const urlRes: any = await recordsApi.getUploadUrl({
            file_name: fileName,
            content_type: 'image/jpeg',
          })

          await uploadToCOS(filePath, urlRes.upload_url)

          const recordRes: any = await recordsApi.create({
            file_key: urlRes.file_key,
            file_type: 'image',
            source: 'camera',
          })

          wx.hideLoading()
          wx.showToast({ title: 'AI 识别中...', icon: 'none', duration: 2000 })

          resolve({ recordId: recordRes.id })
        } catch (err) {
          wx.hideLoading()
          wx.showToast({ title: '上传失败', icon: 'none' })
          reject(err)
        }
      },
      fail: () => reject(new Error('cancelled')),
    })
  })
}

/**
 * ★ Batch upload flow:
 * Choose up to 9 images → upload each → create records → return all recordIds
 */
export function batchUpload(options?: {
  maxCount?: number
  projectId?: number
}): Promise<{ recordIds: number[] }> {
  const maxCount = options?.maxCount || 9
  const projectId = options?.projectId

  return new Promise((resolve, reject) => {
    wx.chooseMedia({
      count: maxCount,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      camera: 'back',
      success: async (res) => {
        const files = res.tempFiles
        const total = files.length

        wx.showLoading({ title: `上传中 0/${total}`, mask: true })

        const recordIds: number[] = []
        let success = 0
        let failed = 0

        for (let i = 0; i < files.length; i++) {
          const filePath = files[i].tempFilePath
          const fileName = filePath.split('/').pop() || `photo_${i}.jpg`

          try {
            const urlRes: any = await recordsApi.getUploadUrl({
              file_name: fileName,
              content_type: 'image/jpeg',
            })

            await uploadToCOS(filePath, urlRes.upload_url)

            const createData: any = {
              file_key: urlRes.file_key,
              file_type: 'image',
              source: 'camera',
            }
            if (projectId) {
              createData.project_id = projectId
            }
            const recordRes: any = await recordsApi.create(createData)

            recordIds.push(recordRes.id)
            success++
          } catch (err) {
            console.error(`Upload failed for file ${i}:`, err)
            failed++
          }

          wx.showLoading({ title: `上传中 ${i + 1}/${total}`, mask: true })
        }

        wx.hideLoading()

        if (success > 0) {
          const msg = failed > 0
            ? `${success} 张上传成功，${failed} 张失败`
            : `${success} 张上传成功，AI 识别中...`
          wx.showToast({ title: msg, icon: 'none', duration: 2500 })
        } else {
          wx.showToast({ title: '上传失败', icon: 'none' })
        }

        if (recordIds.length > 0) {
          resolve({ recordIds })
        } else {
          reject(new Error('All uploads failed'))
        }
      },
      fail: () => reject(new Error('cancelled')),
    })
  })
}


/**
 * ★ 新增：上传音频文件到 COS
 * 1. 获取预签名 URL
 * 2. 读取音频文件 → PUT 到 COS
 * 3. 返回 file_key
 */
export async function uploadAudioToCOS(
  tempFilePath: string
): Promise<{ fileKey: string }> {
  const fileName = `voice_${Date.now()}.mp3`

  // 1. 获取预签名上传 URL
  const urlRes: any = await recordsApi.getUploadUrl({
    file_name: fileName,
    content_type: 'audio/mpeg',
  })

  // 2. 上传到 COS
  await uploadToCOS(tempFilePath, urlRes.upload_url, 'audio/mpeg')

  return { fileKey: urlRes.file_key }
}


/**
 * 通用 COS 上传函数（支持图片和音频）
 */
function uploadToCOS(
  filePath: string,
  uploadUrl: string,
  contentType: string = 'image/jpeg'
): Promise<void> {
  return new Promise((resolve, reject) => {
    const fs = wx.getFileSystemManager()
    fs.readFile({
      filePath,
      success: (res) => {
        wx.request({
          url: uploadUrl,
          method: 'PUT',
          header: {
            'Content-Type': contentType,
          },
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

/**
 * Poll AI recognition status every 3 seconds
 */
export function pollAIStatus(
  recordId: number,
  onComplete: (data: any) => void,
  onError?: (err: any) => void,
  maxAttempts = 20,
) {
  let attempts = 0

  const poll = () => {
    attempts++
    recordsApi.getStatus(recordId).then((res: any) => {
      if (res.ai_status === 'completed') {
        onComplete(res)
      } else if (res.ai_status === 'failed') {
        onError?.(new Error('AI识别失败'))
      } else if (attempts < maxAttempts) {
        setTimeout(poll, 3000)
      } else {
        onError?.(new Error('识别超时'))
      }
    }).catch((err: any) => {
      if (attempts < maxAttempts) {
        setTimeout(poll, 3000)
      } else {
        onError?.(err)
      }
    })
  }

  setTimeout(poll, 2000)
}

/**
 * Poll batch AI status
 */
export function pollBatchAIStatus(
  recordIds: number[],
  onAllComplete: () => void,
  maxAttempts = 30,
) {
  const pending = new Set(recordIds)
  let attempts = 0

  const poll = () => {
    if (pending.size === 0) {
      onAllComplete()
      return
    }
    attempts++
    if (attempts > maxAttempts) {
      onAllComplete()
      return
    }

    const checks = Array.from(pending).map((id) =>
      recordsApi.getStatus(id).then((res: any) => {
        if (res.ai_status === 'completed' || res.ai_status === 'failed') {
          pending.delete(id)
        }
      }).catch(() => {})
    )

    Promise.all(checks).then(() => {
      if (pending.size === 0) {
        onAllComplete()
      } else {
        setTimeout(poll, 3000)
      }
    })
  }

  setTimeout(poll, 3000)
}

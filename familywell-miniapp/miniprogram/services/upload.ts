import { recordsApi } from './api'

/**
 * Complete upload flow:
 * 1. Choose image from camera/album
 * 2. Get presigned upload URL from backend
 * 3. Upload directly to COS
 * 4. Create record in backend
 * 5. Poll AI status until completed
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
          // Step 1: Get presigned URL
          const urlRes: any = await recordsApi.getUploadUrl({
            file_name: fileName,
            content_type: 'image/jpeg',
          })

          // Step 2: Upload to COS
          await uploadToCOS(filePath, urlRes.upload_url)

          // Step 3: Create record
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

function uploadToCOS(filePath: string, uploadUrl: string): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: uploadUrl,
      filePath,
      name: 'file',
      method: 'PUT',
      header: { 'Content-Type': 'image/jpeg' },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve()
        } else {
          reject(new Error(`COS upload failed: ${res.statusCode}`))
        }
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

  // Start first poll after 2 seconds
  setTimeout(poll, 2000)
}

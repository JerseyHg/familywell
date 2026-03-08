/**
 * utils/helpers.ts — 工具函数
 * ★ Fix: 所有日期/时间显示使用本地时间，避免 UTC 导致用户看到错误日期
 */

/**
 * ★ 安全解析日期字符串为本地 Date 对象
 * "YYYY-MM-DD" 格式的纯日期字符串会被 new Date() 当作 UTC 解析，
 * 导致在非 UTC 时区产生日期偏移。此函数统一将其解析为本地日期。
 * 带时间的 ISO 字符串（如 "2026-03-07T15:30:00Z"）正常使用 new Date() 解析。
 */
export function parseLocalDate(dateStr: string): Date {
  // 纯日期格式 "YYYY-MM-DD"：手动解析为本地日期，避免 UTC 偏移
  if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
    const parts = dateStr.split('-')
    return new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]))
  }
  // ★ Fix: 无时区后缀的 datetime 字符串（如 "2026-03-07T15:30:00"）
  //   后端 created_at 存的是 UTC，Pydantic 序列化时不带 Z，
  //   JS new Date() 会当作本地时间解析，导致跨时区日期偏移。
  //   补上 'Z' 让 JS 按 UTC 解析，getDate()/getMonth() 自动转本地。
  if (/^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(dateStr)) {
    return new Date(dateStr + 'Z')
  }
  // 带时区的字符串（如 "...Z" 或 "...+08:00"）：正常解析
  return new Date(dateStr)
}

/**
 * Format date string to display
 * ★ Fix: 使用 parseLocalDate 避免 UTC 偏移
 */
export function formatDate(dateStr: string, style: 'short' | 'full' = 'short'): string {
  if (!dateStr) return ''
  const d = parseLocalDate(dateStr)
  const now = new Date()

  // 使用本地日期进行比较
  const dLocal = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const nowLocal = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const diffDays = Math.round((nowLocal.getTime() - dLocal.getTime()) / 86400000)

  const month = d.getMonth() + 1
  const day = d.getDate()

  if (style === 'short') {
    if (diffDays === 0) return '今天'
    if (diffDays === 1) return '昨天'
    if (diffDays === 2) return '前天'
    return `${month}/${day}`
  }

  return `${d.getFullYear()}/${month}/${day}`
}

/**
 * Format time "HH:mm"
 */
export function formatTime(timeStr: string): string {
  if (!timeStr) return ''
  return timeStr.slice(0, 5)
}

/**
 * Get weekday label
 */
export function getWeekday(dateStr: string): string {
  const days = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return days[parseLocalDate(dateStr).getDay()]
}

/**
 * Get today's date string "YYYY-MM-DD"
 * ★ Fix: 使用本地时间而不是 UTC（toISOString 会转为 UTC，
 *   在东八区等时区会导致跨日问题，如 23:30 北京时间会被当作第二天）
 */
export function today(): string {
  const d = new Date()
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/**
 * Get date N days ago
 * ★ Fix: 同样使用本地时间
 */
export function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

/**
 * Calculate age from birthday
 */
export function calcAge(birthday: string): number {
  const birth = parseLocalDate(birthday)
  const now = new Date()
  let age = now.getFullYear() - birth.getFullYear()
  const monthDiff = now.getMonth() - birth.getMonth()
  if (monthDiff < 0 || (monthDiff === 0 && now.getDate() < birth.getDate())) {
    age--
  }
  return age
}

/**
 * ★ 新增：格式化 ISO 时间字符串为本地时间显示
 * 如 "2026-03-02T15:30:00Z" → "15:30"（东八区 → "23:30"）
 */
export function formatLocalTime(isoStr: string): string {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  const hours = String(d.getHours()).padStart(2, '0')
  const minutes = String(d.getMinutes()).padStart(2, '0')
  return `${hours}:${minutes}`
}

/**
 * ★ 新增：格式化 ISO 时间字符串为本地日期+时间
 * 如 "2026-03-02T15:30:00Z" → "3/2 23:30"
 */
export function formatLocalDateTime(isoStr: string): string {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  const month = d.getMonth() + 1
  const day = d.getDate()
  const hours = String(d.getHours()).padStart(2, '0')
  const minutes = String(d.getMinutes()).padStart(2, '0')
  return `${month}/${day} ${hours}:${minutes}`
}

/**
 * Category label mapping
 */
export const CATEGORY_MAP: Record<string, { label: string; icon: string; color: string }> = {
  checkup:      { label: '体检报告', icon: '📊', color: '#E8F0FE' },
  lab:          { label: '检验报告', icon: '🔬', color: '#E8F5EE' },
  prescription: { label: '处方',     icon: '📝', color: '#FFF3E8' },
  insurance:    { label: '保险',     icon: '🛡️', color: '#EDE8FF' },
  visit:        { label: '就诊记录', icon: '🏥', color: '#FEE8E8' },
  food:         { label: '食物',     icon: '🍽️', color: '#FFF8EC' },
  bp_reading:   { label: '血压',     icon: '❤️', color: '#FEE8E8' },
  weight:       { label: '体重',     icon: '⚖️', color: '#E8F5EE' },
  other:        { label: '其他',     icon: '📄', color: '#F0F2F5' },
}

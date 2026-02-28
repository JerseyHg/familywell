/**
 * Format date string to display
 */
export function formatDate(dateStr: string, style: 'short' | 'full' = 'short'): string {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  const now = new Date()
  const month = d.getMonth() + 1
  const day = d.getDate()

  if (style === 'short') {
    // Today → "今天", Yesterday → "昨天", else "M/D"
    const diff = Math.floor((now.getTime() - d.getTime()) / 86400000)
    if (diff === 0) return '今天'
    if (diff === 1) return '昨天'
    if (diff === 2) return '前天'
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
  return days[new Date(dateStr).getDay()]
}

/**
 * Get today's date string "YYYY-MM-DD"
 */
export function today(): string {
  return new Date().toISOString().slice(0, 10)
}

/**
 * Get date N days ago
 */
export function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

/**
 * Calculate age from birthday
 */
export function calcAge(birthday: string): number {
  const birth = new Date(birthday)
  const now = new Date()
  let age = now.getFullYear() - birth.getFullYear()
  const monthDiff = now.getMonth() - birth.getMonth()
  if (monthDiff < 0 || (monthDiff === 0 && now.getDate() < birth.getDate())) {
    age--
  }
  return age
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

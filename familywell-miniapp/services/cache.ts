/**
 * services/cache.ts — 前端缓存服务
 * ─────────────────────────────────
 * Stale-While-Revalidate 模式：
 * 1. 先返回缓存数据（立即显示）
 * 2. 后台刷新数据（静默更新）
 * 3. 数据变化时通知页面更新
 */

interface CacheEntry<T = any> {
  data: T
  timestamp: number
  version: number
}

interface CacheConfig {
  /** 缓存有效时间（毫秒），默认 5 分钟 */
  ttl: number
  /** 是否持久化到 wx.storage，默认 true */
  persist: boolean
}

const DEFAULT_TTL = 5 * 60 * 1000   // 5 分钟
const PROFILE_TTL = 30 * 60 * 1000  // 30 分钟（profile 很少变）
const HOME_TTL = 2 * 60 * 1000      // 2 分钟（home 数据变化较频繁）
const FAMILY_TTL = 10 * 60 * 1000   // 10 分钟
const PROJECTS_TTL = 5 * 60 * 1000  // 5 分钟

// 内存缓存（比 wx.storage 更快）
const memoryCache: Record<string, CacheEntry> = {}

// 正在进行的请求（防止重复请求）
const pendingRequests: Record<string, Promise<any>> = {}

/** 缓存 key 常量 */
export const CACHE_KEYS = {
  PROFILE: 'cache:profile',
  HOME_DATA: 'cache:home',
  FAMILY: 'cache:family',
  FAMILY_MEMBERS: 'cache:family_members',
  PROJECTS: 'cache:projects',
  RECORDS_COUNT: 'cache:records_count',
}

/** 各 key 对应的 TTL */
const KEY_TTL: Record<string, number> = {
  [CACHE_KEYS.PROFILE]: PROFILE_TTL,
  [CACHE_KEYS.HOME_DATA]: HOME_TTL,
  [CACHE_KEYS.FAMILY]: FAMILY_TTL,
  [CACHE_KEYS.FAMILY_MEMBERS]: FAMILY_TTL,
  [CACHE_KEYS.PROJECTS]: PROJECTS_TTL,
  [CACHE_KEYS.RECORDS_COUNT]: DEFAULT_TTL,
}

/**
 * 获取缓存（优先内存，其次 storage）
 */
function getCache<T>(key: string): CacheEntry<T> | null {
  // 1. 先查内存
  if (memoryCache[key]) {
    return memoryCache[key] as CacheEntry<T>
  }

  // 2. 再查持久化存储
  try {
    const stored = wx.getStorageSync(key)
    if (stored) {
      // 回填到内存缓存
      memoryCache[key] = stored
      return stored as CacheEntry<T>
    }
  } catch { /* ignore */ }

  return null
}

/**
 * 写入缓存（同时写内存和 storage）
 */
function setCache<T>(key: string, data: T): void {
  const entry: CacheEntry<T> = {
    data,
    timestamp: Date.now(),
    version: (memoryCache[key]?.version || 0) + 1,
  }

  // 内存缓存
  memoryCache[key] = entry

  // 持久化（异步，不阻塞）
  try {
    wx.setStorage({ key, data: entry })
  } catch { /* ignore */ }
}

/**
 * 判断缓存是否新鲜
 */
function isFresh(key: string): boolean {
  const entry = getCache(key)
  if (!entry) return false
  const ttl = KEY_TTL[key] || DEFAULT_TTL
  return Date.now() - entry.timestamp < ttl
}

/**
 * 清除指定缓存
 */
export function invalidateCache(...keys: string[]): void {
  for (const key of keys) {
    delete memoryCache[key]
    try {
      wx.removeStorageSync(key)
    } catch { /* ignore */ }
  }
}

/**
 * 清除所有缓存（登出时调用）
 */
export function clearAllCache(): void {
  const allKeys = Object.values(CACHE_KEYS)
  for (const key of allKeys) {
    delete memoryCache[key]
    try {
      wx.removeStorage({ key })
    } catch { /* ignore */ }
  }
}

/**
 * 核心方法：Stale-While-Revalidate
 *
 * @param key      缓存 key
 * @param fetcher  实际请求函数
 * @param onUpdate 数据更新回调（用于后台刷新后更新页面）
 * @returns        立即返回缓存数据（如果有），同时后台刷新
 */
export function swr<T>(
  key: string,
  fetcher: () => Promise<T>,
  onUpdate?: (data: T) => void,
): Promise<T> {
  const cached = getCache<T>(key)

  // 如果缓存新鲜，直接返回，不发请求
  if (cached && isFresh(key)) {
    return Promise.resolve(cached.data)
  }

  // 如果有缓存但不新鲜，立即返回旧数据，后台刷新
  if (cached) {
    // 后台刷新（去重）
    _backgroundRefresh(key, fetcher, onUpdate)
    return Promise.resolve(cached.data)
  }

  // 没有缓存，必须等待请求完成
  return _dedupFetch(key, fetcher)
}

/**
 * 强制刷新缓存（pull-to-refresh 时使用）
 */
export function forceRefresh<T>(
  key: string,
  fetcher: () => Promise<T>,
): Promise<T> {
  // 清除缓存，重新请求
  delete memoryCache[key]
  delete pendingRequests[key]
  return _dedupFetch(key, fetcher)
}

/**
 * 后台刷新（不影响当前显示）
 */
function _backgroundRefresh<T>(
  key: string,
  fetcher: () => Promise<T>,
  onUpdate?: (data: T) => void,
): void {
  if (pendingRequests[key]) return // 已有请求在进行

  pendingRequests[key] = fetcher()
    .then((data) => {
      setCache(key, data)
      if (onUpdate) onUpdate(data)
      return data
    })
    .catch((err) => {
      console.warn(`[Cache] background refresh failed for ${key}:`, err)
    })
    .finally(() => {
      delete pendingRequests[key]
    })
}

/**
 * 去重请求：同一 key 只发一次请求
 */
function _dedupFetch<T>(
  key: string,
  fetcher: () => Promise<T>,
): Promise<T> {
  if (pendingRequests[key]) {
    return pendingRequests[key]
  }

  pendingRequests[key] = fetcher()
    .then((data) => {
      setCache(key, data)
      return data
    })
    .finally(() => {
      delete pendingRequests[key]
    })

  return pendingRequests[key]
}

/**
 * 直接获取缓存数据（同步，用于初始渲染）
 */
export function getCached<T>(key: string): T | null {
  const entry = getCache<T>(key)
  return entry ? entry.data : null
}

/**
 * 数据变更后的缓存失效快捷方法
 */
export const invalidation = {
  /** 用户修改了 profile */
  onProfileChange() {
    invalidateCache(CACHE_KEYS.PROFILE, CACHE_KEYS.HOME_DATA)
  },
  /** 新增/修改了记录 */
  onRecordChange() {
    invalidateCache(CACHE_KEYS.HOME_DATA, CACHE_KEYS.RECORDS_COUNT, CACHE_KEYS.PROJECTS)
  },
  /** 药物相关变更 */
  onMedicationChange() {
    invalidateCache(CACHE_KEYS.HOME_DATA)
  },
  /** 家庭相关变更 */
  onFamilyChange() {
    invalidateCache(CACHE_KEYS.FAMILY, CACHE_KEYS.FAMILY_MEMBERS)
  },
}

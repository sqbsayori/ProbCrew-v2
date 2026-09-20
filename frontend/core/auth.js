/**
 * `core/auth.js` —— 令牌 / 角色（`docs/03 §5.1`）。
 *
 * `docs/02 §7.4` 的"存哪"表把三样东西判给 `localStorage`，**键名是它写死的**：
 * `dsh.token`（会话令牌）· `dsh.model_key`（模型凭据，N14）· `dsh.endpoint`（端点配置，N16）。
 * ★ 本模块**只动这三个键**：`localStorage` 里多出第四个键，就是一次没记账的口径扩张。
 *
 * ★ **当前用户不落 `localStorage`** —— §7.4 的表里没有它。刷新后角色由服务端
 * `/api/me`（W1）重新取；在那之前 `role()` 是 `null`，界面按"未登录"处理。
 */

import { emit, EVENTS } from './bus.js'

/** `docs/02 §7.4` 的三个键名（唯一来源，别处不许写字面量）。 */
export const STORAGE_KEYS = Object.freeze({
  TOKEN: 'dsh.token',
  MODEL_KEY: 'dsh.model_key',
  ENDPOINT: 'dsh.endpoint',
})

/** 服务端返回的当前用户（内存态；刷新即丢，见文件头）。 */
let currentUser = null

function read(key) {
  try {
    return localStorage.getItem(key)
  } catch {
    return null // 隐私模式等：读不到就当没有，不抛给调用方
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch (error) {
    console.warn(`[auth] 写 localStorage 失败（${key}）`, error)
  }
}

function drop(key) {
  try {
    localStorage.removeItem(key)
  } catch {
    /* 同上 */
  }
}

// ---- 会话令牌 ---------------------------------------------------------------

/** 当前会话令牌（明文；服务端只存 sha256 —— `contracts/user.schema.json`）。 */
export function token() {
  return read(STORAGE_KEYS.TOKEN)
}

/** 当前用户对象（内存态），或 `null`。 */
export function user() {
  return currentUser
}

/** 当前角色：`'student'` / `'admin'` / `null`（未登录或刷新后未取回）。 */
export function role() {
  return currentUser?.role ?? null
}

/** 是否管理员（`contracts/user.schema.json` 的 `role` 枚举只有 student / admin）。 */
export function isAdmin() {
  return role() === 'admin'
}

/**
 * 记录一次成功登录：令牌落 `localStorage`、用户进内存，并广播 `auth:changed`。
 *
 * @param {{ token: string, user?: object }} session
 */
export function setSession({ token: nextToken, user: nextUser = null }) {
  if (!nextToken) throw new TypeError('auth.setSession: token 不能为空')
  write(STORAGE_KEYS.TOKEN, nextToken)
  currentUser = nextUser
  const payload = { token: nextToken, user: currentUser, role: role() }
  emit(EVENTS.AUTH_CHANGED, payload)
  return payload
}

/** 只更新内存里的用户（`/api/me` 回来后用），不动令牌。 */
export function setUser(nextUser) {
  currentUser = nextUser ?? null
  const payload = { token: token(), user: currentUser, role: role() }
  emit(EVENTS.AUTH_CHANGED, payload)
  return payload
}

/**
 * 清会话：登出 / 401 / 改密 / 禁用 / 删除都走这里（`docs/02 §1.4` 第 5 条）。
 * ★ **不动模型凭据** —— 它是用户的额度，不随会话撤销而失效（`docs/02 §1.4` 第 3 条）。
 */
export function clearSession() {
  drop(STORAGE_KEYS.TOKEN)
  currentUser = null
  emit(EVENTS.AUTH_CHANGED, { token: null, user: null, role: null })
}

// ---- 模型凭据与端点配置（N14 / N16）-----------------------------------------

/** 模型凭据（随请求走 `X-Model-Key` 头；服务端不落任何介质）。 */
export function modelKey() {
  return read(STORAGE_KEYS.MODEL_KEY)
}

export function setModelKey(value) {
  if (value === null || value === undefined || value === '') drop(STORAGE_KEYS.MODEL_KEY)
  else write(STORAGE_KEYS.MODEL_KEY, String(value))
}

export function clearModelKey() {
  drop(STORAGE_KEYS.MODEL_KEY)
}

/**
 * 端点配置：`{ provider, model, base_url }`（`docs/02 §7.4`）。
 * @returns {{ provider?: string, model?: string, base_url?: string } | null}
 */
export function endpoint() {
  const raw = read(STORAGE_KEYS.ENDPOINT)
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    console.warn('[auth] dsh.endpoint 不是合法 JSON，按未配置处理')
    return null
  }
}

export function setEndpoint(config) {
  if (config === null || config === undefined) drop(STORAGE_KEYS.ENDPOINT)
  else write(STORAGE_KEYS.ENDPOINT, JSON.stringify(config))
}

export function clearEndpoint() {
  drop(STORAGE_KEYS.ENDPOINT)
}

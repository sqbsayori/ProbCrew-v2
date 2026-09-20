/**
 * `core/router.js` —— 含 `hashchange` 的路由（`docs/03 §5.1`）。
 *
 * `docs/02 §7.1` 的三条实现要求，本模块逐条落实：
 * ① 监听 `hashchange` **与** `popstate`（旧仓只写 hash、无人监听 ⇒ 返回键只变 URL 不变视图）；
 * ② 视图状态从 URL **单向派生**（本模块只把 URL 解析成 `{ path, query, hash }` 广播出去，
 *    不保存"当前视图"这种第二份状态）；
 * ③ **URL 是渲染状态的来源，不是权限或计数的来源**（权限一律问服务端）。
 */

import { emit, EVENTS } from './bus.js'

const routes = new Map()
let fallback = null
let started = false

/** 把 `#/home` / `/home` / `home` 一律归一成 `/home`；空哈希视作 `/`。 */
export function normalizePath(input) {
  let path = String(input ?? '').trim()
  if (path.startsWith('#')) path = path.slice(1)
  if (path === '') return '/'
  if (!path.startsWith('/')) path = `/${path}`
  return path.split('?')[0].replace(/\/+$/, '') || '/'
}

/**
 * 解析一个哈希（含查询串）。
 *
 * @param {string} [hash] 缺省取 `location.hash`
 * @returns {{ path: string, query: Record<string, string>, hash: string }}
 */
export function parse(hash = location.hash) {
  const raw = String(hash ?? '')
  const withoutHash = raw.startsWith('#') ? raw.slice(1) : raw
  const [rawPath, rawQuery = ''] = withoutHash.split('?')
  const query = {}
  for (const [key, value] of new URLSearchParams(rawQuery)) query[key] = value
  return { path: normalizePath(rawPath), query, hash: raw }
}

/**
 * 注册一条路由。
 *
 * @param {string} path 如 `#/practice`（查询串不参与匹配 —— `docs/02 §7.1` 的
 *   `#/practice?kc=…&item=…&hint=2` 与 `#/practice` 是同一条路由）
 * @param {(route: { path: string, query: Record<string, string>, hash: string }) => void} handler
 * @returns {() => void} 注销函数
 */
export function register(path, handler) {
  if (typeof handler !== 'function') throw new TypeError(`router.register(${path}): handler 必须是函数`)
  const key = normalizePath(path)
  routes.set(key, handler)
  return () => routes.delete(key)
}

/** 未命中任何路由时的兜底（缺省只广播一条提示，不做重定向 —— 重定向是外壳的决定）。 */
export function setFallback(handler) {
  fallback = handler
}

/** 解析当前 URL 并把对应视图渲染出来（幂等；重复调用同一路由也安全）。 */
export function render() {
  const route = parse()
  const handler = routes.get(route.path)
  if (handler) handler(route)
  else if (fallback) fallback(route)
  else emit(EVENTS.NOTICE, { message: `未知路由：${route.path}`, tone: 'warn' })
  emit(EVENTS.ROUTE_CHANGED, route)
  return route
}

/** 改哈希（唯一的导航出口）。同哈希重复导航也会触发一次 `render()`。 */
export function navigate(target) {
  const next = String(target ?? '').startsWith('#') ? String(target) : `#${normalizePath(target)}`
  if (location.hash === next) render()
  else location.hash = next
}

/** 开始监听。幂等 —— 重复调用只挂一次监听器。 */
export function start() {
  if (started) return
  started = true
  window.addEventListener('hashchange', render)
  window.addEventListener('popstate', render) // ①：返回键/前进键
  render()
}

/** 停止监听（用例与热重载用）。 */
export function stop() {
  if (!started) return
  started = false
  window.removeEventListener('hashchange', render)
  window.removeEventListener('popstate', render)
}

/** 已注册的路由路径（诊断 / 用例用）。 */
export function registered() {
  return Array.from(routes.keys())
}

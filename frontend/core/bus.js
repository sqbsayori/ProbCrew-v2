/**
 * `core/bus.js` —— 事件（`docs/03 §5.1`）。
 *
 * ★ **`features/*` 之间不得互相 import，只能经本模块通信**（`docs/03 §5.1` 的依赖方向）。
 * 因此**事件名是一份冻结的公开接口**：本文件是它的唯一来源，别处不许写字面量事件名。
 *
 * 命名约定：`<主体>:<动作>`，全小写，冒号分隔。新增事件 = **在这里加一条 + 在
 * `frontend/README`/本注释里说明载荷形状**，不许在调用处随手写字符串。
 */

/** 事件名登记表（★ 冻结；2026-09-20 首次取值，卡 1 · W0a）。 */
export const EVENTS = Object.freeze({
  /** 外壳初始化完成。载荷：`{ version }`。 */
  APP_READY: 'app:ready',
  /** 路由变化（哈希路由的唯一广播）。载荷：`{ path, query, hash }`。 */
  ROUTE_CHANGED: 'route:changed',
  /** 登录态 / 角色变化。载荷：`{ user, role, token } | { user: null }`。 */
  AUTH_CHANGED: 'auth:changed',
  /** `core/store` 的内存态变更。载荷：`{ key, value, previous }`。 */
  STATE_CHANGED: 'state:changed',
  /** `core/api` 解析出一个统一错误体（`contracts/error.schema.json`）。载荷：`ApiError`。 */
  API_ERROR: 'api:error',
  /** 面向用户的一句话提示（含 F17/F18 一类**显式降级话术**）。载荷：`{ message, tone }`。 */
  NOTICE: 'notice',
})

/** 事件名 → 处理器集合。 */
const handlers = new Map()

/**
 * 订阅。返回**退订函数**（调用即 `off`）。
 *
 * @param {string} name `EVENTS` 里的一个值
 * @param {(payload: any) => void} handler
 * @returns {() => void}
 */
export function on(name, handler) {
  if (typeof handler !== 'function') throw new TypeError(`bus.on(${name}): handler 必须是函数`)
  if (!handlers.has(name)) handlers.set(name, new Set())
  handlers.get(name).add(handler)
  return () => off(name, handler)
}

/** 退订。 */
export function off(name, handler) {
  handlers.get(name)?.delete(handler)
}

/** 只触发一次的订阅。 */
export function once(name, handler) {
  const unsubscribe = on(name, (payload) => {
    unsubscribe()
    handler(payload)
  })
  return unsubscribe
}

/**
 * 广播。
 *
 * ★ **一个处理器抛错不掀掉整轮广播** —— 逐个捕获并 `console.error`：
 * 事件是"通知"，不是"调用链"，一个页面的 bug 不该让另一个页面收不到通知。
 */
export function emit(name, payload) {
  const set = handlers.get(name)
  if (!set) return
  for (const handler of Array.from(set)) {
    try {
      handler(payload)
    } catch (error) {
      console.error(`[bus] 处理器抛错，事件=${name}`, error)
    }
  }
}

/** 当前订阅数（诊断 / 用例用）。 */
export function listenerCount(name) {
  return handlers.get(name)?.size ?? 0
}

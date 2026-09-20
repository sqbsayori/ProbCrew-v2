/**
 * `core/store.js` —— 内存态（`docs/03 §5.1`）。
 *
 * `docs/02 §7.4` 把"跨页面临时态（当前 run、未提交的作答）"判给本模块：
 * **刷新即丢是可接受的** —— 它们本来就不该是持久状态。
 * 持久的东西各有归处：令牌/凭据/端点配置在 `localStorage`（`core/auth.js`），
 * 界面位置在 URL（`core/router.js`），持久学习数据只在服务端。
 */

import { emit, EVENTS } from './bus.js'

/**
 * 建一个键值内存态。
 *
 * @param {Record<string, any>} [initial]
 * @returns {{
 *   get: (key: string, fallback?: any) => any,
 *   has: (key: string) => boolean,
 *   set: (key: string, value: any) => any,
 *   update: (key: string, updater: (previous: any) => any) => any,
 *   remove: (key: string) => void,
 *   keys: () => string[],
 *   snapshot: () => Record<string, any>,
 *   clear: () => void,
 *   subscribe: (key: string, handler: (value: any, previous: any) => void) => () => void,
 * }}
 */
export function createStore(initial = {}) {
  const state = new Map(Object.entries(initial))
  const listeners = new Map()

  function notify(key, value, previous) {
    emit(EVENTS.STATE_CHANGED, { key, value, previous })
    for (const handler of listeners.get(key) ?? []) {
      try {
        handler(value, previous)
      } catch (error) {
        console.error(`[store] 订阅者抛错，key=${key}`, error)
      }
    }
  }

  return {
    get: (key, fallback = undefined) => (state.has(key) ? state.get(key) : fallback),
    has: (key) => state.has(key),
    set(key, value) {
      const previous = state.get(key)
      state.set(key, value)
      notify(key, value, previous)
      return value
    },
    update(key, updater) {
      return this.set(key, updater(state.get(key)))
    },
    remove(key) {
      const previous = state.get(key)
      state.delete(key)
      notify(key, undefined, previous)
    },
    keys: () => Array.from(state.keys()),
    snapshot: () => Object.fromEntries(state),
    clear() {
      for (const key of Array.from(state.keys())) this.remove(key)
    },
    subscribe(key, handler) {
      if (!listeners.has(key)) listeners.set(key, new Set())
      listeners.get(key).add(handler)
      return () => listeners.get(key)?.delete(handler)
    },
  }
}

/** 全站共用的那一个 store（`docs/03 §5.1` 只列了 `store` 一个模块，没有多实例的说法）。 */
export const store = createStore()

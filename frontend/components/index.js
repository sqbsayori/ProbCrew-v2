/**
 * `frontend/components/index.js` —— 复用 UI 的**公开入口**（`docs/03 §5.1`）。
 *
 * 归属：`frontend/components/**` 是**架构与集成**的（`docs/05 §3`）；
 * `features/*` 只能用、不能改（域四的"不做什么"）。
 * 依赖方向：`components` 只能依赖 `core`，**不得 import `features/`**（`docs/03 §3.1`）。
 *
 * ★ 本轮（W0a）交出的是**最小可用集**：卡片 / 表格 / 空态 / 骨架态 / 提示条 + 图表占位。
 * 全部用 `core/dom.js` 组装（**没有一处 `innerHTML`** —— R-E）。
 * ★ 视觉一律走 `styles/components.css` 的类名，色值只来自 `tokens.css`（R-D）。
 *
 * 用法示例（域四照 `contracts/*.schema.json` 的 `examples` 当假数据即可）：
 * ```js
 * import { card, table, emptyState, skeleton, notice } from '../components/index.js'
 *
 * main.append(card({
 *   title: '错题本',
 *   body: table({
 *     columns: [{ key: 'stem', label: '题目' }, { key: 'kc', label: '知识点' }],
 *     rows: [{ stem: '贝叶斯公式…', kc: 'kc_bayes' }],
 *     empty: '还没有错题',
 *   }),
 * }))
 * ```
 */

import { el, text } from '../core/dom.js'

/** 组件清单（供文档与"这一版有什么"的检索用）。 */
export const COMPONENTS = Object.freeze([
  'card',
  'table',
  'emptyState',
  'skeleton',
  'notice',
  'chart',
])

/**
 * 卡片。
 * @param {{ title?: string, actions?: Node[], body?: Node|string|Array<Node|string>, footer?: Node|string }} [options]
 * @returns {HTMLElement}
 */
export function card({ title, actions = [], body = null, footer = null } = {}) {
  const node = el('section', { class: 'card' })
  if (title !== undefined || actions.length > 0) {
    const head = el('header', { class: 'card__head' })
    if (title !== undefined) head.append(el('h2', { class: 'card__title', text: title }))
    if (actions.length > 0) head.append(el('div', { class: 'card__actions' }, actions))
    node.append(head)
  }
  if (body !== null) node.append(el('div', { class: 'card__body' }, asNodes(body)))
  if (footer !== null) node.append(el('footer', { class: 'card__foot' }, asNodes(footer)))
  return node
}

/**
 * 表格。`rows` 为空时渲染 `empty` 空态（而不是一张没有行的空表）。
 *
 * @param {{
 *   columns: { key: string, label: string, render?: (row: any) => Node|string }[],
 *   rows?: any[],
 *   empty?: string,
 *   caption?: string,
 * }} options
 * @returns {HTMLElement}
 */
export function table({ columns = [], rows = [], empty = '暂无数据', caption } = {}) {
  if (rows.length === 0) return emptyState({ title: empty })
  const headCells = columns.map((column) => el('th', { scope: 'col', text: column.label }))
  const bodyRows = rows.map((row) => el('tr', {}, columns.map((column) => {
    const value = column.render ? column.render(row) : row[column.key]
    return el('td', {}, value instanceof Node ? [value] : [text(value)])
  })))
  const node = el('table', { class: 'table' }, [
    ...(caption ? [el('caption', { class: 'page__subtitle', text: caption })] : []),
    el('thead', {}, [el('tr', {}, headCells)]),
    el('tbody', {}, bodyRows),
  ])
  return node
}

/**
 * 空态。
 * @param {{ title?: string, hint?: string }} [options]
 * @returns {HTMLElement}
 */
export function emptyState({ title = '暂无内容', hint = '' } = {}) {
  return el('div', { class: 'empty' }, [
    el('p', { class: 'empty__title', text: title }),
    ...(hint ? [el('p', { class: 'empty__hint', text: hint })] : []),
  ])
}

/**
 * 骨架态（加载中）。
 * @param {{ lines?: number }} [options]
 * @returns {HTMLElement}
 */
export function skeleton({ lines = 3 } = {}) {
  const rows = []
  for (let index = 0; index < lines; index += 1) {
    rows.push(el('div', {
      class: index === lines - 1 ? 'skeleton__line skeleton__line--short' : 'skeleton__line',
    }))
  }
  return el('div', { class: 'skeleton', role: 'status', 'aria-busy': 'true' }, rows)
}

/**
 * 提示条 —— 也用于 F17/F18 一类**显式降级话术**（`docs/06 §4` 出口条件 2：不得静默）。
 * @param {{ message: string, tone?: 'info'|'success'|'warn'|'danger' }} options
 * @returns {HTMLElement}
 */
export function notice({ message, tone = 'info' }) {
  return el('div', { class: `notice notice--${tone}`, role: tone === 'danger' ? 'alert' : 'status' }, [
    el('p', { text: message }),
  ])
}

/**
 * 图表 —— ★ **本轮只有占位**：图表实现属后续工作包（`docs/03 §5.1` 的组件清单里有它，
 * 但 W0a 的判据只要"组件骨架可用"）。调用它会得到一个写明"未实现"的占位块。
 * @param {{ title?: string }} [options]
 * @returns {HTMLElement}
 */
export function chart({ title = '图表' } = {}) {
  return el('div', { class: 'chart-placeholder', role: 'img', 'aria-label': `${title}（未实现）` }, [
    text(`${title}：组件骨架已就位，渲染实现属后续工作包`),
  ])
}

/** 把字符串 / 节点 / 数组统一成节点数组。 */
function asNodes(value) {
  if (Array.isArray(value)) return value.map((item) => (item instanceof Node ? item : text(item)))
  return [value instanceof Node ? value : text(value)]
}

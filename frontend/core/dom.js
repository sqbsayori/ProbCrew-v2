/**
 * `core/dom.js` —— 创建元素 / 转义（`docs/03 §5.1`）。
 *
 * ★ **R-E 的施加面**：前端**不得用 `innerHTML` 拼用户内容**（`docs/02 §1.6`）。
 * 因此本模块**只提供文本与元素两类出口，一个 `innerHTML` 出口都没有** ——
 * 想插 HTML 就得自己写 `innerHTML`，而那是 grep 能抓到的形状（门禁 R-E）。
 */

/** 把任意值转成字符串（`null` / `undefined` ⇒ 空串）。 */
export function text(value) {
  return value === null || value === undefined ? '' : String(value)
}

/**
 * 转义 HTML 特殊字符。
 *
 * ★ 本函数的用途**只有一处**：把**可信的自制模板**里的占位符替换成数据。
 * 凡是用户内容，一律走 {@link setText} / {@link el} 的文本子节点，不要走这里。
 */
export function escapeHtml(value) {
  return text(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

/** `document.querySelector` 的包装（`root` 缺省为 `document`）。 */
export function qs(selector, root = document) {
  return root.querySelector(selector)
}

/** `document.querySelectorAll` → 真数组。 */
export function qsa(selector, root = document) {
  return Array.from(root.querySelectorAll(selector))
}

/**
 * 创建元素。
 *
 * @param {string} tag 标签名
 * @param {object} [attrs] `class` / `dataset` / `text` / 其余按属性写入；
 *   `text` 与 `dataset` 之外的键一律 `setAttribute`。
 * @param {Array<Node|string>} [children]
 * @returns {HTMLElement}
 */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag)
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue
    if (key === 'text') node.textContent = text(value)
    else if (key === 'dataset') Object.assign(node.dataset, value)
    else if (key === 'class') node.className = text(value)
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value)
    } else if (value === true) node.setAttribute(key, '')
    else node.setAttribute(key, text(value))
  }
  for (const child of children) {
    node.append(typeof child === 'string' ? document.createTextNode(child) : child)
  }
  return node
}

/** 创建文本节点（**唯一的"把数据显示出来"的出口**）。 */
export function txt(value) {
  return document.createTextNode(text(value))
}

/** 设置元素的文本内容 —— 永远走 `textContent`，不用 `innerHTML`。 */
export function setText(node, value) {
  node.textContent = text(value)
  return node
}

/** 清空一个元素的所有子节点。 */
export function clear(node) {
  node.replaceChildren()
  return node
}

/** 用新内容整体替换一个元素的子节点。 */
export function mount(parent, ...children) {
  parent.replaceChildren(...children)
  return parent
}

/** 文档片段（批量组装的廉价容器）。 */
export function frag(children = []) {
  const fragment = document.createDocumentFragment()
  for (const child of children) {
    fragment.append(typeof child === 'string' ? document.createTextNode(child) : child)
  }
  return fragment
}

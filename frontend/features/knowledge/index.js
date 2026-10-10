/**
 * 知识页（W4 · T2）
 * ================
 * 两件事：① **F9 的上溯**（沿 `depends_on` 反向可达的前置集合）② 接口未就绪时**如实降级**。
 *
 * ★ **上溯在前端做**（`docs/06 §7` 的"不做什么"④ —— 已裁定：F9 的 BFS 不在服务端实现）。
 * ★ **全量 + 计数、不设门槛、不截断**（`docs/01` F9 · `docs/02 §2.2` 规则 3 · **决策 6A**）。
 * ★ 状态**单向从 URL 派生**（`#/knowledge?kc=&view=graph`，`docs/02 §7.1`）—— 本模块不留第二份状态；
 * DOM 只经 `core/dom`（**R-E：不用 `innerHTML`**）。
 * ★ **不造假数据**（N2）：真实端点不可用时用**契约 `examples`** 当假数据，并在页面上写明
 * "接口尚未实现" —— 两者必须一眼分得开。
 */
import { el, mount } from '../../core/dom.js'
import { register } from '../../core/router.js'
import { get } from '../../core/api.js'
import { card, emptyState, notice } from '../../components/index.js'

if (!document.querySelector('link[data-knowledge-style]')) {
  document.head.append(el('link', {
    rel: 'stylesheet',
    href: '/features/knowledge/styles.css',
    dataset: { knowledgeStyle: 'true' },
  }))
}

/**
 * 契约 `knowledge.schema.json` 的 `examples`（3 个节点）。
 * ★ 其中 `kc_bayes → kc_total_probability` 是**契约里刻意的缺环**（`nodes` 里没有这个节点）——
 * 它正是"缺环必须如实降级"这条判据的现成样本。
 */
const FALLBACK_TREE = {
  nodes: [
    { kc_id: 'kc_semantics', name: '样本空间与事件运算', chapter: 1, depends_on: [] },
    { kc_id: 'kc_conditional_prob', name: '条件概率', chapter: 3, depends_on: ['kc_semantics'] },
    {
      kc_id: 'kc_bayes',
      name: '贝叶斯公式',
      chapter: 3,
      depends_on: ['kc_conditional_prob', 'kc_total_probability'],
    },
  ],
}

/**
 * 契约 `attempt.schema.json` 的 `examples` 里出现过的 `kc_ids`（`GET /api/me/wrong` 未就绪时用）。
 * ★ `kc_random_variable` **不在**上面的图谱里 —— 起点不在图谱也要如实报出，不得静默。
 */
const FALLBACK_WRONG_KC_IDS = ['kc_bayes', 'kc_conditional_prob', 'kc_random_variable']

/** F9 的空集文案 —— 原文见 `docs/01` F9（**不是** F11 的"数据不足，暂不显示薄弱点"）。 */
export const EMPTY_TRACE_MESSAGE = '未定位到前置薄弱点'

/** F17 / F18 的降级话术（`docs/01` 的闭环完整性行给出 F17 的原文；F18 由接口状态如实说明）。 */
const DEGRADE = {
  F17: '本次未拆分题目',
  F18: '公式检索接口尚未实现（属域2）—— 如实降级，不编造条目',
}

/** 翻页上限 —— 防"下一页永远有"时的死循环；触顶就**如实说没取全**（不冒充全量）。 */
const MAX_PAGES = 20

/**
 * F9 的上溯（**纯函数，无 DOM**）：从起点集合沿 `depends_on` 反向可达的前置集合。
 *
 * 口径逐条（计划 §4.6 写死）：同一路径不重复计数 · **集合不含起点自身** ·
 * 缺环（引用了图谱里不存在的知识点）**不计入**并单独报出 · 起点不在图谱也单独报出。
 *
 * @param {Array<{kc_id: string, depends_on?: string[]}>} nodes
 * @param {string[]} startIds
 * @returns {{ids: string[], missing: string[], unknownStarts: string[]}}
 */
export function tracePrerequisites(nodes, startIds) {
  const index = new Map(nodes.map((node) => [node.kc_id, node]))
  const queue = []
  for (const start of startIds) {
    for (const dependency of index.get(start)?.depends_on ?? []) queue.push(dependency)
  }
  const reachable = new Set()
  const missing = new Set()
  while (queue.length) {
    const id = queue.shift()
    if (reachable.has(id)) continue                 // ★ 同一路径不重复计数
    if (!index.has(id)) { missing.add(id); continue }  // ★ 缺环：不计入集合，如实报出
    reachable.add(id)
    for (const dependency of index.get(id).depends_on ?? []) queue.push(dependency)
  }
  for (const start of startIds) reachable.delete(start)  // ★ 集合不含起点自身
  return {
    ids: [...reachable].sort(),
    missing: [...missing].sort(),
    unknownStarts: startIds.filter((id) => !index.has(id)).sort(),
  }
}

/**
 * 取图谱：真端点不可用 ⇒ 契约 `examples` 假数据 + **如实标注**（N2）。
 * @returns {Promise<{nodes: any[], live: boolean, reason: string}>}
 */
async function loadTree() {
  try {
    const tree = await get('/api/knowledge/tree')
    if (tree && Array.isArray(tree.nodes)) return { nodes: tree.nodes, live: true, reason: '' }
    return { nodes: FALLBACK_TREE.nodes, live: false, reason: '响应形状不符合契约' }
  } catch (error) {
    return { nodes: FALLBACK_TREE.nodes, live: false, reason: error?.message ?? '接口不可用' }
  }
}

/**
 * 取错题知识点集合：`GET /api/me/wrong` 是 **cursor 分页** ⇒ **翻页直到 `next_cursor` 为空**
 * 才算"全量"；中途失败或触到翻页上限 ⇒ `complete: false`，**不得用部分数据冒充全量**。
 */
async function loadWrongKcIds() {
  const ids = new Set()
  let cursor = ''
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const query = new URLSearchParams({ limit: '20' })
    if (cursor) query.set('cursor', cursor)
    const body = await get(`/api/me/wrong?${query.toString()}`)
    for (const item of body?.items ?? []) {
      for (const kcId of item?.kc_ids ?? []) ids.add(kcId)
    }
    cursor = String(body?.next_cursor ?? '')
    if (!cursor) return { ids: [...ids], complete: true }
  }
  return { ids: [...ids], complete: false }
}

/** 渲染器 —— 状态全部来自 `route`（URL），不留第二份。 */
export function render(route) {
  const kc = String(route?.query?.kc ?? '').trim()
  const view = String(route?.query?.view ?? '').trim() || 'graph'
  const body = el('div', { class: 'knowledge' }, [
    el('p', { class: 'knowledge__source', text: '正在加载知识图谱…' }),
  ])
  mount(document.querySelector('#app-main') ?? document.body, body)
  void paint(body, { kc, view })
}

async function paint(body, { kc, view }) {
  const tree = await loadTree()
  const blocks = [sourceLine(tree)]
  if (view !== 'graph') {
    blocks.push(notice({ message: `视图 ${view} 尚未实现 —— 本页只实现 graph`, tone: 'warn' }))
  }
  blocks.push(await traceBlock(tree, kc))
  blocks.push(notice({ message: DEGRADE.F18, tone: 'info' }))
  mount(body, ...blocks)
}

function sourceLine(tree) {
  return el('p', {
    class: 'knowledge__source',
    text: tree.live
      ? '数据来源：GET /api/knowledge/tree（真实接口）'
      : `数据来源：契约 examples 假数据 —— 接口尚未实现（${tree.reason}）`,
  })
}

/** F9 的展示：全量 + 计数；空集与缺环都**显式**说话（不得静默）。 */
async function traceBlock(tree, kc) {
  const origin = await resolveStarts(kc)
  const traced = tracePrerequisites(tree.nodes, origin.ids)
  const countLine = el('p', {
    class: 'knowledge__count',
    text: `沿依赖上溯到 ${traced.ids.length} 个前置知识点（起点 ${origin.label}，全量显示、不截断）`,
  })
  const blocks = [card({ title: '依赖上溯（F9）', body: countLine })]

  if (!traced.ids.length) {
    blocks.push(emptyState({ title: EMPTY_TRACE_MESSAGE, hint: '起点本身没有可上溯的前置知识点。' }))
  } else {
    blocks.push(el('ul', { class: 'knowledge__list' }, traced.ids.map((id) => el('li', {}, [
      el('span', { class: 'knowledge__chip', text: `${label(tree.nodes, id)}（${id}）` }),
    ]))))
  }
  if (traced.missing.length) {
    blocks.push(notice({
      message: `缺环：依赖链引用了图谱里不存在的知识点 —— ${traced.missing.join(' · ')}（不计入上溯集合）`,
      tone: 'warn',
    }))
  }
  if (traced.unknownStarts.length) {
    blocks.push(notice({
      message: `起点不在图谱里 —— ${traced.unknownStarts.join(' · ')}（如实报出，不当作"没有薄弱点"）`,
      tone: 'warn',
    }))
  }
  if (origin.note) blocks.push(notice({ message: origin.note, tone: 'warn' }))
  return el('div', {}, blocks)
}

/** 起点优先级（计划 §4.6 写死）：`?kc=` → 错题知识点集合 → 都没有 ⇒ 空集文案那条路。 */
async function resolveStarts(kc) {
  if (kc) return { ids: [kc], label: `?kc=${kc}`, note: '' }
  try {
    const wrong = await loadWrongKcIds()
    const note = wrong.complete ? '' : '错题列表未取全（触到翻页上限或中途失败）—— 结果不是全量'
    return { ids: wrong.ids, label: '错题知识点', note }
  } catch (error) {
    return {
      ids: FALLBACK_WRONG_KC_IDS,
      label: '契约 examples 假数据',
      note: `GET /api/me/wrong 尚未实现（${error?.message ?? '接口不可用'}）—— 用契约 examples 演示`,
    }
  }
}

function label(nodes, kcId) {
  return nodes.find((node) => node.kc_id === kcId)?.name ?? kcId
}

register('#/knowledge', render)

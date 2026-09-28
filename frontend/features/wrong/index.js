/**
 * 错题本（W2 · 人四）
 * ==================
 * 读取 GET /api/me/wrong，按知识点分组；不把 expected 当作可公开内容展示。
 * 接口未就绪时保留页面结构与明确说明，不伪造错题数据。
 */
import { el, mount, qs } from '../../core/dom.js'
import { register, navigate } from '../../core/router.js'
import { get } from '../../core/api.js'
import { card, table, notice, emptyState, skeleton } from '../../components/index.js'

if (!document.querySelector('link[data-wrong-style]')) {
  document.head.append(el('link', {
    rel: 'stylesheet',
    href: '/features/wrong/styles.css',
    dataset: { wrongStyle: 'true' },
  }))
}

const KC_LABELS = {
  kc_bayes: '贝叶斯公式',
  kc_conditional_prob: '条件概率',
  kc_conditional_probability: '条件概率',
  kc_random_variable: '随机变量',
  kc_expectation: '数学期望',
}

let renderSeq = 0
let mainEl = null
let state = null

function compact(values) {
  return values.filter(Boolean)
}

function kcLabel(kc) {
  return KC_LABELS[kc] || String(kc || '未标注知识点').replace(/^kc_/, '').replaceAll('_', ' ')
}

function itemId(row) {
  return row?.item_id || row?.prob_id || row?.problem?.prob_id || ''
}

function itemStem(row) {
  return row?.stem || row?.problem?.stem || row?.item?.stem || itemId(row) || '题目内容暂不可用'
}

function itemKcs(row) {
  return Array.isArray(row?.kc_ids) ? row.kc_ids : []
}

function itemSource(row) {
  return row?.source === 'wrong_review' ? '错题重做' : '练习'
}

function wrongReason(row) {
  if (row?.mistake_matched && row?.mistake_id) return row.mistake_id
  if (row?.answer_raw) return `作答：${row.answer_raw}`
  return '待补充错因'
}

function redoHref(row, kc) {
  const query = new URLSearchParams()
  if (itemId(row)) query.set('item', itemId(row))
  if (kc) query.set('kc', kc)
  return `#/practice?${query.toString()}`
}

function draw() {
  if (!mainEl || !state) return
  mount(mainEl, buildPage(state))
}

function buildFilter() {
  const input = el('input', {
    class: 'wrong-filter__input',
    type: 'search',
    placeholder: '按知识点筛选，例如 kc_bayes',
    value: state.kc || '',
  })
  const form = el('form', { class: 'wrong-filter' }, [
    input,
    el('button', { class: 'button button--primary', type: 'submit', text: '筛选' }),
  ])
  form.addEventListener('submit', (event) => {
    event.preventDefault()
    const next = new URLSearchParams()
    const kc = input.value.trim()
    if (kc) next.set('kc', kc)
    navigate(`#/wrong?${next.toString()}`)
  })
  return form
}

function buildGroup(kc, rows) {
  const body = table({
    columns: [
      { key: 'stem', label: '题目', render: (row) => el('span', { text: itemStem(row) }) },
      { key: 'source', label: '来源', render: itemSource },
      { key: 'reason', label: '错因 / 作答', render: (row) => el('span', { class: 'wrong-answer', text: wrongReason(row) }) },
      {
        key: 'redo',
        label: '操作',
        render: (row) => el('a', { class: 'wrong-link', href: redoHref(row, kc), text: '重做' }),
      },
    ],
    rows,
    empty: '暂无错题',
  })
  return card({ title: `${kcLabel(kc)}（${rows.length}）`, body })
}

function buildGroups() {
  if (!state.items.length) return emptyState({
    title: '暂无错题',
    hint: '答错且被判定链记录后，错题会按知识点出现在这里。',
  })
  const groups = new Map()
  for (const row of state.items) {
    const key = itemKcs(row)[0] || 'kc_unknown'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(row)
  }
  return el('div', { class: 'wrong-groups' }, Array.from(groups.entries()).map(([kc, rows]) => buildGroup(kc, rows)))
}

function buildPage(current) {
  const notices = compact([
    current.error ? notice({ message: current.error, tone: 'danger' }) : null,
    current.notice ? notice({ message: current.notice, tone: 'info' }) : null,
  ])
  return el('section', { class: 'page wrong-page', dataset: { block: 'wrong-shell' } }, [
    el('header', { class: 'page__header' }, [
      el('div', { class: 'page__titles' }, [
        el('h1', { class: 'page__title', text: '错题本' }),
        el('p', { class: 'page__subtitle', text: '按知识点分组 · 保留作答与错因 · 可重做' }),
      ]),
      el('div', { class: 'page__toolbar' }, [
        el('a', { class: 'button button--primary', href: '#/practice', text: '去练习' }),
      ]),
    ]),
    ...notices,
    card({ title: '按知识点筛选', body: buildFilter() }),
    buildGroups(),
  ])
}

function buildLoading() {
  return el('section', { class: 'page wrong-page' }, [
    el('header', { class: 'page__header' }, [
      el('div', { class: 'page__titles' }, [
        el('h1', { class: 'page__title', text: '错题本' }),
        el('p', { class: 'page__subtitle', text: '正在读取错题…' }),
      ]),
    ]),
    card({ title: '错题记录', body: skeleton({ lines: 4 }) }),
  ])
}

async function fetchWrong(route) {
  const query = new URLSearchParams({ limit: '50' })
  if (route.query.kc) query.set('kc', route.query.kc)
  const page = await get(`/api/me/wrong?${query.toString()}`)
  return Array.isArray(page?.items) ? page.items : []
}

export async function renderWrong(route = { query: {} }) {
  const main = qs('#app-main')
  if (!main) return
  const seq = ++renderSeq
  mainEl = main
  mount(main, buildLoading())

  let items = []
  let error = ''
  let noticeText = ''
  try {
    items = await fetchWrong(route)
  } catch (err) {
    error = err?.status === 404
      ? '错题接口尚未就绪（属 W2 · 后端与数据）。'
      : `错题加载失败：${err?.message || '未知错误'}`
    noticeText = '当前只保留页面结构；接口接通后会自动显示真实错题。'
  }

  if (seq !== renderSeq) return
  state = {
    kc: route.query.kc || '',
    items,
    error,
    notice: noticeText,
  }
  draw()
}

register('#/wrong', renderWrong)

/**
 * 刷题页（W2 · 人四）
 * ==================
 * 只调用契约规定的接口；后端未就绪时显式降级为契约示例，不假装已拿到真实数据。
 */
import { el, mount, qs } from '../../core/dom.js'
import { register, navigate } from '../../core/router.js'
import { get, post } from '../../core/api.js'
import { card, table, notice, emptyState, skeleton } from '../../components/index.js'

if (!document.querySelector('link[data-practice-style]')) {
  document.head.append(el('link', {
    rel: 'stylesheet',
    href: '/features/practice/styles.css',
    dataset: { practiceStyle: 'true' },
  }))
}

const FALLBACK_PROBLEMS = [
  {
    prob_id: 'prob_bayes_0012',
    stem: '某病发病率 1%，检测灵敏度 90%、特异度 90%，检出阳性者患病的概率是多少？',
    kc_ids: ['kc_bayes', 'kc_conditional_prob'],
    pt_id: 'pt_bayes_forward',
    answer_kind: 'value',
    difficulty: 3,
  },
  {
    prob_id: 'prob_expect_0007',
    stem: '某随机变量 X 的密度函数为 f(x)=2x（0<x<1），求 E(X)。',
    kc_ids: ['kc_random_variable', 'kc_expectation'],
    pt_id: 'pt_expectation_integral',
    answer_kind: 'value',
    difficulty: 2,
  },
]

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
let sessionCache = null

function compact(values) {
  return values.filter(Boolean)
}

function sessionId() {
  if (sessionCache) return sessionCache
  const key = 'dsh.practice_session'
  sessionCache = sessionStorage.getItem(key)
  if (!sessionCache) {
    sessionCache = typeof crypto?.randomUUID === 'function'
      ? `ses_${crypto.randomUUID()}`
      : `ses_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
    sessionStorage.setItem(key, sessionCache)
  }
  return sessionCache
}

function kcLabel(kc) {
  return KC_LABELS[kc] || String(kc || '未标注知识点').replace(/^kc_/, '').replaceAll('_', ' ')
}

function problemId(item) {
  return item?.prob_id || item?.item_id || ''
}

function problemStem(item) {
  return item?.stem || item?.problem?.stem || item?.item?.stem || '题目内容暂不可用'
}

function problemKcs(item) {
  return Array.isArray(item?.kc_ids) ? item.kc_ids : []
}

function problemAnswerKind(item) {
  return item?.answer_kind === 'text' ? '文字结论' : '数值 / 闭式表达式'
}

function fallbackMessage(error) {
  if (error?.status === 404) return '题库与判定接口尚未就绪（属 W2 · 后端与数据）；当前显示契约示例，接口接通后自动加载真实题目。'
  return `题目加载失败：${error?.message || '未知错误'}。当前显示契约示例。`
}

function operationMessage(error, operation) {
  if (error?.status === 404) return `${operation}接口尚未就绪（属 W2 · 后端与数据）。`
  return `${operation}失败：${error?.message || '未知错误'}`
}

function practiceHref(item, kc) {
  const query = new URLSearchParams()
  if (problemId(item)) query.set('item', problemId(item))
  if (kc) query.set('kc', kc)
  return `#/practice?${query.toString()}`
}

function draw() {
  if (!mainEl || !state) return
  mount(mainEl, buildPage(state))
}

function buildFilter() {
  const input = el('input', {
    class: 'practice-filter__input',
    type: 'search',
    placeholder: '按知识点筛选，例如 kc_bayes',
    value: state.kc || '',
  })
  const form = el('form', { class: 'practice-filter' }, [
    input,
    el('button', { class: 'button button--primary', type: 'submit', text: '筛选' }),
  ])
  form.addEventListener('submit', (event) => {
    event.preventDefault()
    const next = new URLSearchParams()
    const kc = input.value.trim()
    if (kc) next.set('kc', kc)
    if (state.selected) next.set('item', problemId(state.selected))
    navigate(`#/practice?${next.toString()}`)
  })
  return form
}

function buildProblemList() {
  return table({
    columns: [
      {
        key: 'stem',
        label: '题目',
        render: (row) => el('a', {
          class: 'practice-problem-link',
          href: practiceHref(row, state.kc),
          text: problemStem(row),
        }),
      },
      {
        key: 'kc_ids',
        label: '知识点',
        render: (row) => problemKcs(row).map(kcLabel).join('、') || '未标注',
      },
      { key: 'difficulty', label: '难度', render: (row) => `${row.difficulty ?? '—'}/5` },
    ],
    rows: state.problems,
    empty: '暂无题目',
  })
}

function buildProblem() {
  if (!state.selected) return emptyState({ title: '请选择一道题', hint: '从左侧题目列表中选择，或按知识点筛选。' })
  const item = state.selected
  const meta = el('div', { class: 'practice-meta' }, [
    el('span', { class: 'practice-badge', text: `题型：${item.pt_id || '未提供'}` }),
    el('span', { class: 'practice-badge', text: `答案类型：${problemAnswerKind(item)}` }),
    el('span', { class: 'practice-badge', text: `难度：${item.difficulty ?? '—'}/5` }),
    ...problemKcs(item).map((kc) => el('span', { class: 'practice-badge', text: kcLabel(kc) })),
  ])
  return el('div', { class: 'practice-problem' }, [
    el('p', { class: 'practice-problem__stem', text: problemStem(item) }),
    meta,
  ])
}

function buildAnswer() {
  const unavailable = state.backendUnavailable
  const area = el('textarea', {
    class: 'practice-answer',
    maxlength: 200,
    placeholder: '输入答案，支持文字或 LaTeX；提交后才会看到解析。',
    disabled: unavailable || state.loading,
  })
  area.value = state.answer
  area.addEventListener('input', () => { state.answer = area.value })

  const submit = el('button', {
    class: 'button button--primary',
    type: 'button',
    text: state.loading ? '提交中…' : '提交作答',
    disabled: unavailable || state.loading || !state.selected,
  })
  submit.addEventListener('click', submitAnswer)

  const hint = el('button', {
    class: 'button',
    type: 'button',
    text: state.loading ? '处理中…' : '获取提示',
    disabled: unavailable || state.loading || !state.selected,
  })
  hint.addEventListener('click', requestHint)

  return el('div', {}, [
    area,
    el('div', { class: 'practice-actions' }, [submit, hint]),
  ])
}

function buildHints() {
  if (!state.hints.length) return emptyState({
    title: '还没有使用提示',
    hint: '每次点击只会请求下一级提示；未提交前不会返回完整解析。',
  })
  return el('div', { class: 'practice-hints' }, state.hints.map((hint) => (
    el('p', { class: 'practice-hint', text: `第 ${hint.level} 级（本次已用 ${hint.hint_used} 次）：${hint.content}` })
  )))
}

function buildResult() {
  const result = state.result
  if (!result) return emptyState({ title: '尚未提交', hint: '提交后才能看到判定结果与依据。' })
  if (result.graded === false) return notice({ message: result.message || '该题需人工判定。', tone: 'warn' })
  const lines = [
    el('p', { class: 'practice-result__line', text: result.correct ? '判定：正确' : '判定：错误' }),
    el('p', { class: 'practice-result__line', text: `本题第 ${result.attempt_no} 次作答；本次使用提示 ${result.hint_used} 次。` }),
  ]
  if (result.mistake_matched && result.mistake_id) {
    lines.push(el('p', { class: 'practice-result__line', text: `错因：${result.mistake_id}` }))
  }
  const solution = result.solution
  if (solution?.steps?.length) {
    lines.push(el('ol', { class: 'practice-solution' }, solution.steps.map((step) => el('li', {}, compact([
      step.explanation ? el('span', { text: step.explanation }) : null,
      step.formula ? el('span', { class: 'practice-solution__formula', text: step.formula }) : null,
    ])))))
  }
  if (solution?.explanation) lines.push(el('p', { class: 'practice-result__line', text: solution.explanation }))
  if (!result.correct) lines.push(el('p', { class: 'practice-result__line' }, [
    el('a', { class: 'practice-problem-link', href: '#/wrong', text: '去错题本查看这道题' }),
  ]))
  return el('div', { class: 'practice-result' }, lines)
}

function buildPage(current) {
  const notices = compact([
    current.error ? notice({ message: current.error, tone: 'danger' }) : null,
    current.backendUnavailable ? notice({ message: current.notice, tone: 'info' }) : null,
  ])
  return el('section', { class: 'page practice-page', dataset: { block: 'practice-shell' } }, [
    el('header', { class: 'page__header' }, [
      el('div', { class: 'page__titles' }, [
        el('h1', { class: 'page__title', text: '练习' }),
        el('p', { class: 'page__subtitle', text: '按知识点刷题 · 作答 · 提示 · 判定与依据' }),
      ]),
      el('div', { class: 'page__toolbar' }, [
        el('a', { class: 'button', href: '#/wrong', text: '查看错题本' }),
      ]),
    ]),
    ...notices,
    card({ title: '按知识点筛选', body: buildFilter() }),
    el('div', { class: 'practice-layout' }, [
      card({ title: '题目列表', body: buildProblemList() }),
      el('div', { class: 'practice-workspace' }, [
        card({ title: '当前题目', body: buildProblem() }),
        card({ title: '作答', body: buildAnswer() }),
        card({ title: '提示', body: buildHints() }),
        card({ title: '判定结果', body: buildResult() }),
      ]),
    ]),
  ])
}

function buildLoading() {
  return el('section', { class: 'page practice-page' }, [
    el('header', { class: 'page__header' }, [
      el('div', { class: 'page__titles' }, [
        el('h1', { class: 'page__title', text: '练习' }),
        el('p', { class: 'page__subtitle', text: '正在读取题目…' }),
      ]),
    ]),
    card({ title: '题目列表', body: skeleton({ lines: 4 }) }),
  ])
}

async function submitAnswer() {
  if (!state?.selected || state.backendUnavailable || state.loading) return
  const answer = state.answer.trim()
  if (!answer) {
    state.error = '请先输入答案。'
    draw()
    return
  }
  state.loading = true
  state.error = ''
  draw()
  try {
    state.result = await post('/api/practice/grade', {
      item_id: problemId(state.selected),
      answer,
      duration_ms: Math.max(0, Date.now() - state.startedAt),
      session_id: sessionId(),
    })
  } catch (error) {
    state.error = operationMessage(error, '判定')
  } finally {
    state.loading = false
    draw()
  }
}

async function requestHint() {
  if (!state?.selected || state.backendUnavailable || state.loading) return
  state.loading = true
  state.error = ''
  draw()
  try {
    const hint = await post('/api/practice/hint', {
      item_id: problemId(state.selected),
      session_id: sessionId(),
    })
    state.hints.push(hint)
  } catch (error) {
    state.error = operationMessage(error, '提示')
  } finally {
    state.loading = false
    draw()
  }
}

async function fetchProblems(route) {
  const query = new URLSearchParams({ limit: '30' })
  if (route.query.kc) query.set('kc', route.query.kc)
  const page = await get(`/api/problems?${query.toString()}`)
  return Array.isArray(page?.items) ? page.items : []
}

function chooseProblem(problems, itemId) {
  return problems.find((item) => problemId(item) === itemId) || problems[0] || null
}

export async function renderPractice(route = { query: {} }) {
  const main = qs('#app-main')
  if (!main) return
  const seq = ++renderSeq
  mainEl = main
  mount(main, buildLoading())

  let problems = FALLBACK_PROBLEMS
  let backendUnavailable = false
  let noticeText = '题库接口尚未就绪（属 W2 · 后端与数据）；当前显示契约示例，接口接通后自动加载真实题目。'
  try {
    const realProblems = await fetchProblems(route)
    problems = realProblems
    noticeText = realProblems.length ? '' : '题库暂无题目，请等待内容资产加载。'
  } catch (error) {
    backendUnavailable = true
    noticeText = fallbackMessage(error)
  }

  if (seq !== renderSeq) return
  state = {
    kc: route.query.kc || '',
    problems,
    selected: chooseProblem(problems, route.query.item),
    answer: '',
    hints: [],
    result: null,
    loading: false,
    error: '',
    backendUnavailable,
    notice: noticeText,
    startedAt: Date.now(),
  }
  draw()
}

register('#/practice', renderPractice)

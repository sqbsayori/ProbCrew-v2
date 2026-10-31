/**
 * 刷题页（W2 · 人四）
 * ==================
 * 只调用契约规定的接口；后端未就绪时显式降级为契约示例，不假装已拿到真实数据。
 */
import { el, mount, qs } from '../../core/dom.js'
import { register, navigate } from '../../core/router.js'
import { get, post, stream } from '../../core/api.js'
import { endpoint } from '../../core/auth.js'
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
let activeChatAbort = null

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


function stopChat() {
  if (activeChatAbort) {
    activeChatAbort.abort()
    activeChatAbort = null
  }
  if (state?.chat) {
    state.chat.status = 'error'
    state.chat.error = '已停止本次答疑。'
    draw()
  }
}

function renderChatFrame(frame) {
  const payload = frame?.payload || {}
  const type = frame?.type || 'unknown'
  const body = []
  if (type === 'run.start') {
    body.push(el('span', { text: `答疑已开始（run_id：${payload.run_id || '—'}）` }))
  } else if (type === 'context.resolved') {
    body.push(el('span', { text: payload.context ? '已带入当前题目上下文。' : `未带入上下文${payload.reason ? `：${payload.reason}` : ''}。` }))
  } else if (type === 'plan') {
    body.push(el('span', { text: `计划：${payload.intent || '—'}${Array.isArray(payload.steps) ? `（${payload.steps.length} 步）` : ''}` }))
  } else if (type === 'node.start') {
    body.push(el('span', { text: `开始处理：${payload.label || payload.node || '—'}` }))
  } else if (type === 'node.delta') {
    body.push(el('p', { class: 'practice-chat__delta', text: payload.text || '' }))
  } else if (type === 'tool.call') {
    body.push(el('span', { text: `调用工具：${payload.tool || '—'}` }))
  } else if (type === 'tool.result') {
    body.push(el('span', { text: `工具 ${payload.tool || '—'}：${payload.ok ? '成功' : `失败${payload.reason ? `（${payload.reason}）` : ''}`}` }))
  } else if (type === 'artifact') {
    const artifact = payload.payload || {}
    body.push(el('span', { text: `产物：${payload.kind || 'artifact'}${artifact.title ? ` · ${artifact.title}` : ''}` }))
    if (payload.kind === 'decomposition' && (artifact.known || artifact.asked)) {
      body.push(el('dl', { class: 'practice-decomposition' }, [
        el('dt', { text: '已知' }),
        el('dd', { text: artifact.known || '—' }),
        el('dt', { text: '所求' }),
        el('dd', { text: artifact.asked || '—' }),
        el('dt', { text: '适用模型' }),
        el('dd', { text: artifact.model_id || '未匹配模型' }),
      ]))
      if (!artifact.model_id && artifact.note) {
        body.push(el('p', { class: 'practice-chat__delta', text: artifact.note }))
      }
    }
    if (Array.isArray(artifact.steps)) {
      body.push(el('ol', { class: 'practice-chat__steps' }, artifact.steps.map((step) => el('li', {}, compact([
        step.explanation ? el('span', { text: step.explanation }) : null,
        step.formula ? el('span', { class: 'practice-solution__formula', text: step.formula }) : null,
      ])))))
    }
  } else if (type === 'node.end') {
    body.push(el('span', { text: `处理结束：${payload.node || '—'}（${payload.ok ? '成功' : '失败'}）` }))
  } else if (type === 'verification.report') {
    const report = payload.report || {}
    body.push(el('span', { text: `验证：${report.levelLabel || report.level || '未报告'}${payload.ok ? ' · 通过' : ' · 未通过'}` }))
    if (payload.summary) body.push(el('p', { class: 'practice-chat__delta', text: payload.summary }))
  } else if (type === 'hitl.request') {
    body.push(el('span', { text: `需要人工确认：${payload.draft || '请查看请求'}` }))
    if (Array.isArray(payload.options)) body.push(el('span', { text: `可选操作 ${payload.options.length} 项。` }))
  } else if (type === 'hitl.resolved') {
    body.push(el('span', { text: `人工确认完成：${payload.action || '已处理'}` }))
  } else if (type === 'done') {
    body.push(el('strong', { text: `完成：${payload.final_answer || payload.status || '完成'}` }))
  } else if (type === 'error') {
    body.push(el('strong', { text: `错误：${payload.code || 'error'}${payload.message ? ` · ${payload.message}` : ''}` }))
  } else {
    body.push(el('span', { text: '收到未知事件类型，已按安全文本保留。' }))
  }
  return el('li', { class: 'practice-chat__event', dataset: { chatType: type } }, [
    el('span', { class: 'practice-chat__type', text: `#${frame?.seq ?? '—'} ${type}` }),
    ...body,
  ])
}

function modelErrorMessage(code) {
  if (code === 'model_key_missing') return '还没有配置模型凭据，请先到设置页填写 Key。'
  if (code === 'model_key_invalid') return '模型凭据无效或已被撤销，请到设置页重新填写。'
  if (code === 'model_unreachable') return '模型端点暂不可达或响应无效，可稍后重试。'
  if (code === 'model_quota_exceeded') return '模型额度不足或欠费；费用由凭据持有人承担。'
  return ''
}

async function startChat() {
  if (!state?.selected || state.chat.status === 'running') return
  const configuredEndpoint = endpoint()
  if (!configuredEndpoint) {
    state.chat.error = '尚未配置模型端点。请先到设置页配置 provider / model / base_url。'
    state.chat.errorCode = 'model_key_missing'
    draw()
    return
  }
  const query = state.chat.query.trim()
  if (!query) {
    state.chat.error = '请先输入要问的问题。'
    state.chat.errorCode = ''
    draw()
    return
  }

  const controller = new AbortController()
  const chatState = state.chat
  activeChatAbort = controller
  chatState.frames = []
  chatState.status = 'running'
  chatState.error = ''
  chatState.errorCode = ''
  draw()

  try {
    const result = await stream('/api/chat', {
      query,
      context: {
        text: problemStem(state.selected),
        refs: [{ kind: 'problem', id: problemId(state.selected) }],
      },
      endpoint: configuredEndpoint,
    }, {
      signal: controller.signal,
      onFrame: (frame) => {
        chatState.frames.push(frame)
        if (state?.chat === chatState) draw()
      },
    })
    if (state?.chat === chatState) {
      chatState.status = result.endType === 'done' ? 'done' : 'error'
      if (result.endType === 'error') {
        const end = [...chatState.frames].reverse().find((frame) => frame?.type === 'error')
        chatState.errorCode = end?.payload?.code || ''
        chatState.error = modelErrorMessage(chatState.errorCode) || '答疑流返回了 error 终帧。'
      }
    }
  } catch (error) {
    if (state?.chat === chatState) {
      chatState.status = 'error'
      chatState.errorCode = error?.code || ''
      chatState.error = error?.name === 'AbortError'
        ? '已停止本次答疑。'
        : modelErrorMessage(error?.code) || `答疑失败：${error?.message || '未知错误'}`
    }
  } finally {
    if (activeChatAbort === controller) activeChatAbort = null
    if (state?.chat === chatState) draw()
  }
}

function buildChat() {
  const chat = state.chat
  const configuredEndpoint = endpoint()
  const unavailable = !state.selected || !configuredEndpoint
  const running = chat.status === 'running'
  const query = el('textarea', {
    class: 'practice-chat__input',
    maxlength: 2000,
    placeholder: '针对当前题目提问，例如：为什么要用条件概率？',
    disabled: unavailable || running,
  })
  query.value = chat.query
  query.addEventListener('input', () => { chat.query = query.value })

  const actions = [
    el('button', {
      class: 'button button--primary',
      type: 'submit',
      text: running ? '答疑进行中…' : '开始答疑',
      disabled: unavailable || running,
    }),
  ]
  if (running) {
    actions.push(el('button', {
      class: 'button',
      type: 'button',
      text: '停止答疑',
      onclick: stopChat,
    }))
  }
  const form = el('form', { class: 'practice-chat__form' }, [
    query,
    el('div', { class: 'practice-actions' }, actions),
  ])
  form.addEventListener('submit', (event) => {
    event.preventDefault()
    startChat()
  })

  const settingsAction = ['model_key_missing', 'model_key_invalid'].includes(chat.errorCode)
    ? el('a', { class: 'button', href: '#/settings?tab=model', text: '去设置模型' })
    : null
  const notices = compact([
    !state.selected ? notice({ message: '请先选择一道题，再把题目作为上下文提问。', tone: 'info' }) : null,
    state.selected && !configuredEndpoint ? notice({ message: '尚未配置模型端点，请先到设置页配置 provider / model / base_url。', tone: 'warn' }) : null,
    chat.error ? notice({ message: chat.error, tone: 'danger' }) : null,
    settingsAction,
  ])
  const statusText = running ? '答疑进行中…' : chat.status === 'done' ? '答疑完成' : chat.status === 'error' ? '答疑失败' : '尚未开始'
  return el('div', { class: 'practice-chat' }, [
    ...notices,
    form,
    el('p', { class: 'practice-chat__status', text: statusText }),
    chat.frames.length
      ? el('ol', { class: 'practice-chat__events' }, chat.frames.map(renderChatFrame))
      : emptyState({ title: '暂无答疑事件', hint: '提交问题后，事件会按服务端返回顺序逐步显示。' }),
  ])
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
        card({ title: '答疑（W3）', body: buildChat() }),
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
  if (activeChatAbort) { activeChatAbort.abort(); activeChatAbort = null }
  state = {
    kc: route.query.kc || '',
    problems,
    selected: chooseProblem(problems, route.query.item),
    answer: '',
    hints: [],
    result: null,
    chat: { query: '', frames: [], status: 'idle', error: '', errorCode: '' },
    loading: false,
    error: '',
    backendUnavailable,
    notice: noticeText,
    startedAt: Date.now(),
  }
  draw()
}

register('#/practice', renderPractice)

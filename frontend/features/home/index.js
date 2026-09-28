/**
 * 首页（W2 接真实摘要版）
 * =====================
 * W0 的静态样板间保留为降级视图；W2 接口可用时读取 /api/me/progress 与 /api/problems。
 * 所有渲染仍只使用 core / components，不修改公共组件；任何失败都显式说明，不伪造数据。
 */
import { el, mount, qs } from '../../core/dom.js'
import { register } from '../../core/router.js'
import { get } from '../../core/api.js'
import { card, table, notice, emptyState, chart } from '../../components/index.js'

if (!document.querySelector('link[data-home-style]')) {
  document.head.append(el('link', {
    rel: 'stylesheet',
    href: '/features/home/styles.css',
    dataset: { homeStyle: 'true' },
  }))
}

const EXAMPLE_PROBLEMS = [
  {
    prob_id: 'prob_bayes_0012',
    stem: '某病发病率 1%，检测灵敏度 90%、特异度 90%，检出阳性者患病的概率是多少？',
    kc_ids: ['kc_bayes', 'kc_conditional_prob'],
    difficulty: 3,
  },
  {
    prob_id: 'prob_expect_0007',
    stem: '某随机变量 X 的密度函数为 f(x)=2x（0<x<1），求 E(X)。',
    kc_ids: ['kc_random_variable', 'kc_expectation'],
    difficulty: 2,
  },
]

const EXAMPLE_MASTERY = {
  total_attempts: 37,
  topics: [
    { topic: 'kc_bayes', attempts: 12, correct: 7, accuracy: 0.5833 },
    { topic: 'kc_conditional_prob', attempts: 9, correct: 9, accuracy: 1 },
    { topic: 'kc_random_variable', attempts: 0, correct: 0, accuracy: null },
  ],
  weak: { available: false, note: '数据不足，暂不显示薄弱点' },
}

const KC_LABELS = {
  kc_bayes: '贝叶斯公式',
  kc_conditional_prob: '条件概率',
  kc_conditional_probability: '条件概率',
  kc_random_variable: '随机变量',
  kc_expectation: '数学期望',
}

let homeSeq = 0

function kcLabel(kc) {
  return KC_LABELS[kc] || String(kc || '未标注知识点').replace(/^kc_/, '').replaceAll('_', ' ')
}

function stat(label, value, hint) {
  return el('div', { class: 'home-stat' }, [
    el('span', { class: 'home-stat__label', text: label }),
    el('strong', { class: 'home-stat__value', text: value }),
    hint ? el('span', { class: 'home-stat__hint', text: hint }) : null,
  ].filter(Boolean))
}

function actionLink(href, label, primary = false) {
  return el('a', { class: primary ? 'button button--primary' : 'button', href, text: label })
}

function totalCorrect(topics) {
  return topics.reduce((sum, topic) => sum + (Number(topic.correct) || 0), 0)
}

function totalTopicAttempts(topics) {
  return topics.reduce((sum, topic) => sum + (Number(topic.attempts) || 0), 0)
}

function recentProblems(page) {
  return Array.isArray(page?.items) ? page.items : []
}

function buildOverview(progress) {
  const topics = Array.isArray(progress?.topics) ? progress.topics : []
  const practiced = topics.filter((topic) => Number(topic.attempts) > 0).length
  const attempts = totalTopicAttempts(topics)
  const accuracy = attempts ? Math.round((totalCorrect(topics) / attempts) * 1000) / 10 : null
  return el('div', { class: 'home-stats', dataset: { block: 'home-overview' } }, [
    stat('累计作答', String(progress?.total_attempts ?? 0), '来自 /api/me/progress'),
    stat('已练习知识点', String(practiced), '按 attempt 聚合'),
    stat('当前正确率', accuracy === null ? '—' : `${accuracy}%`, '按知识点聚合'),
  ])
}

function buildProblemTable(problems) {
  return table({
    columns: [
      { key: 'stem', label: '题目' },
      {
        key: 'kc_ids',
        label: '知识点',
        render: (row) => (Array.isArray(row.kc_ids) ? row.kc_ids.map(kcLabel).join('、') : '未标注'),
      },
      { key: 'difficulty', label: '难度', render: (row) => `${row.difficulty ?? '—'}/5` },
    ],
    rows: problems,
    empty: '暂无推荐题目',
  })
}

function buildMasteryTable(progress) {
  const topics = Array.isArray(progress?.topics) ? progress.topics : []
  return table({
    columns: [
      { key: 'topic', label: '知识点', render: (row) => kcLabel(row.topic) },
      { key: 'attempts', label: '作答次数', render: (row) => String(row.attempts ?? 0) },
      {
        key: 'accuracy',
        label: '正确率',
        render: (row) => row.accuracy === null ? '未作答' : `${Math.round(row.accuracy * 100)}%`,
      },
    ],
    rows: topics,
    empty: '暂无学习记录',
  })
}

function buildPage(progress, problems, message) {
  const weak = progress?.weak || EXAMPLE_MASTERY.weak
  return el('section', { class: 'page home-page', dataset: { block: 'home-shell' } }, [
    el('header', { class: 'page__header' }, [
      el('div', { class: 'page__titles' }, [
        el('h1', { class: 'page__title', text: '首页' }),
        el('p', { class: 'page__subtitle', text: '概率论与数理统计伴学助手 · 学习摘要' }),
      ]),
      el('div', { class: 'page__toolbar' }, [
        actionLink('#/practice', '开始练习', true),
        actionLink('#/knowledge', '查看知识图谱'),
      ]),
    ]),
    notice({ message, tone: 'info' }),
    el('div', { class: 'home-grid' }, [
      card({ title: '学习概览', body: buildOverview(progress) }),
      card({
        title: '快捷入口',
        body: el('div', { class: 'home-actions' }, [
          actionLink('#/practice', '按知识点刷题', true),
          actionLink('#/wrong', '复习错题'),
          actionLink('#/settings', '配置模型'),
        ]),
      }),
    ]),
    card({ title: '推荐练习', body: buildProblemTable(problems) }),
    card({
      title: '知识点掌握',
      body: el('div', {}, [
        buildMasteryTable(progress),
        weak.available
          ? notice({ message: weak.note, tone: 'warn' })
          : emptyState({ title: '薄弱点暂不可用', hint: weak.note }),
      ]),
    }),
    card({ title: '学习趋势', body: chart({ title: '近 7 天学习趋势' }) }),
  ])
}

export function renderHome() {
  const main = qs('#app-main')
  if (!main) return
  const seq = ++homeSeq
  mount(main, buildPage(EXAMPLE_MASTERY, EXAMPLE_PROBLEMS, '正在读取真实学习摘要；接口未就绪时显示契约示例。'))

  void (async () => {
    try {
      const [progress, problemPage] = await Promise.all([
        get('/api/me/progress'),
        get('/api/problems?limit=2'),
      ])
      if (seq !== homeSeq) return
      const problems = recentProblems(problemPage)
      mount(main, buildPage(progress, problems.length ? problems : EXAMPLE_PROBLEMS, '学习摘要来自后端真实数据。'))
    } catch (error) {
      if (seq !== homeSeq) return
      const message = error?.status === 404
        ? '学习摘要接口尚未就绪（属 W2 · 后端与数据）；当前显示契约示例。'
        : `真实学习摘要暂不可用：${error?.message || '未知错误'}。当前显示契约示例。`
      mount(main, buildPage(EXAMPLE_MASTERY, EXAMPLE_PROBLEMS, message))
    }
  })()
}

register('#/home', renderHome)

/**
 * 首页静态首屏（W0d）
 * ==================
 * 本页只演示契约 examples 与公共 components 的组合用法：
 *   - 不发真实请求；
 *   - 不写业务判定或持久化逻辑；
 *   - 不修改 components / styles 的实现；
 *   - 页面自有样式只放在 features/home/styles.css。
 */
import { el, mount, qs } from '../../core/dom.js';
import { register } from '../../core/router.js';
import { card, table, notice, emptyState, chart } from '../../components/index.js';

if (!document.querySelector('link[data-home-style]')) {
  document.head.append(el('link', {
    rel: 'stylesheet',
    href: '/features/home/styles.css',
    dataset: { homeStyle: 'true' },
  }));
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
];

const EXAMPLE_MASTERY = {
  total_attempts: 37,
  topics: [
    { topic: 'kc_bayes', attempts: 12, correct: 7, accuracy: 0.5833 },
    { topic: 'kc_conditional_prob', attempts: 9, correct: 9, accuracy: 1 },
    { topic: 'kc_random_variable', attempts: 0, correct: 0, accuracy: null },
  ],
  weak: { available: false, note: '数据不足，暂不显示薄弱点' },
};

const KC_LABELS = {
  kc_bayes: '贝叶斯公式',
  kc_conditional_prob: '条件概率',
  kc_random_variable: '随机变量',
  kc_expectation: '数学期望',
};

function stat(label, value, hint) {
  return el('div', { class: 'home-stat' }, [
    el('span', { class: 'home-stat__label', text: label }),
    el('strong', { class: 'home-stat__value', text: value }),
    hint ? el('span', { class: 'home-stat__hint', text: hint }) : null,
  ].filter(Boolean));
}

function actionLink(href, label, primary = false) {
  return el('a', {
    class: primary ? 'button button--primary' : 'button',
    href,
    text: label,
  });
}

function renderHome() {
  const main = qs('#app-main');
  if (!main) return;
  const overview = el('div', { class: 'home-stats', dataset: { block: 'home-overview' } }, [
    stat('累计作答', String(EXAMPLE_MASTERY.total_attempts), '来自 mastery.schema 示例'),
    stat('已练习知识点', '2', '贝叶斯 · 条件概率'),
    stat('当前正确率', '78.4%', '示例数据，非实时'),
  ]);
  const problemTable = table({
    columns: [
      { key: 'stem', label: '题目' },
      {
        key: 'kc_ids', label: '知识点',
        render: (row) => row.kc_ids.map((id) => KC_LABELS[id] || id).join('、'),
      },
      { key: 'difficulty', label: '难度', render: (row) => row.difficulty + '/5' },
    ],
    rows: EXAMPLE_PROBLEMS,
    empty: '暂无推荐题目',
  });
  const masteryTable = table({
    columns: [
      { key: 'topic', label: '知识点', render: (row) => KC_LABELS[row.topic] || row.topic },
      { key: 'attempts', label: '作答次数' },
      {
        key: 'accuracy', label: '正确率',
        render: (row) => row.accuracy === null ? '未作答' : Math.round(row.accuracy * 100) + '%',
      },
    ],
    rows: EXAMPLE_MASTERY.topics,
    empty: '暂无学习记录',
  });
  const page = el('section', { class: 'page home-page', dataset: { block: 'home-shell' } }, [
    el('header', { class: 'page__header' }, [
      el('div', { class: 'page__titles' }, [
        el('h1', { class: 'page__title', text: '首页' }),
        el('p', { class: 'page__subtitle', text: '概率论与数理统计伴学助手 · 静态首屏' }),
      ]),
      el('div', { class: 'page__toolbar' }, [
        actionLink('#/practice', '开始练习', true),
        actionLink('#/knowledge', '查看知识图谱'),
      ]),
    ]),
    notice({ message: '当前为首屏样张：数据来自 contracts 示例，未调用后端接口。', tone: 'info' }),
    el('div', { class: 'home-grid' }, [
      card({ title: '学习概览', body: overview }),
      card({
        title: '快捷入口',
        body: el('div', { class: 'home-actions' }, [
          actionLink('#/practice', '按知识点刷题', true),
          actionLink('#/wrong', '复习错题'),
          actionLink('#/settings', '配置模型'),
        ]),
      }),
    ]),
    card({ title: '推荐练习', body: problemTable }),
    card({
      title: '知识点掌握',
      body: el('div', {}, [
        masteryTable,
        EXAMPLE_MASTERY.weak.available
          ? notice({ message: EXAMPLE_MASTERY.weak.note, tone: 'warn' })
          : emptyState({ title: '薄弱点暂不可用', hint: EXAMPLE_MASTERY.weak.note }),
      ]),
    }),
    card({ title: '学习趋势', body: chart({ title: '近 7 天学习趋势' }) }),
  ]);
  mount(main, page);
}

register('#/home', renderHome);

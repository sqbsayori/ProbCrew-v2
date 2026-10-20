/**
 * 管理页（W1 · 教师侧）—— `#/admin?tab=students|users|audit&user=usr_xxx`
 * ==========================================================================
 * 三个标签对应三个端点（`docs/02 §6.1` 的管理行 · `contracts/user.schema.json` 的
 * `adminStudentRow` / `adminUserRow` / `auditRow`）：
 *
 * - **学生数据**（数据视图）：逐人的作答计数 / 掌握度 / 最近 20 条作答 —— **不含**任何跨学生聚合；
 * - **账号管理**：状态 / 锁定 / 活跃令牌 + 导入 / 重置密码 / 删除；
 * - **审计**：谁在何时看了 / 改了谁的数据（看审计本身也会留一条 `view_audit`）。
 *
 * ★ **权限不在这里判**：`role()` 只决定**显示什么**，真正的闸门在服务端
 *   （`api/deps.py::require_admin`，学生令牌一律 403 `forbidden`）。URL 里改 `tab` 不会越权 ——
 *   这正是 `docs/02 §7.1` ③「URL 是渲染状态的来源，不是权限的来源」。
 * ★ **刷新后 `role()` 是 null**（当前用户不落 `localStorage`）⇒ 本页先问一次 `/api/me`
 *   再决定渲染什么；那一步失败就按"未登录"处理，不猜。
 * ★ 页面上的数字**只做显示**：不在这里算平均分 / 排名 / 分布（`docs/01 §9` 的排除项）。
 */
import { el, mount, qs } from '../../core/dom.js'
import { register, navigate } from '../../core/router.js'
import { del, get, patch, post } from '../../core/api.js'
import { role, setUser, token } from '../../core/auth.js'
import { card, notice, skeleton, table } from '../../components/index.js'

if (!document.querySelector('link[data-admin-style]')) {
  document.head.append(el('link', {
    rel: 'stylesheet',
    href: '/features/admin/styles.css',
    dataset: { adminStyle: 'true' },
  }))
}

/** 三个标签（`docs/02 §7.1` 的 `#/admin?tab=…`）。 */
const TABS = Object.freeze([
  { id: 'students', label: '学生数据' },
  { id: 'users', label: '账号管理' },
  { id: 'audit', label: '审计' },
])

/** `auditRow.action` 的 8 个取值（契约枚举，只增不改）。 */
const AUDIT_ACTIONS = Object.freeze([
  'view_students', 'view_users', 'view_audit', 'import_users',
  'update_user', 'reset_password', 'delete_user', 'delete_own_data',
])

const PAGE_LIMIT = 50

let mainEl = null
let renderSeq = 0
let state = null

/** 主键后 8 位 —— 同名学生的区分依据（契约 `username` 的复用规则）。 */
function shortId(userId) {
  return String(userId ?? '').slice(-8)
}

function fmtTime(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const date = new Date(Number(seconds) * 1000)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN')
}

function statusLabel(status) {
  return { active: '正常', disabled: '已停用', deleted: '已删除' }[status] ?? String(status ?? '—')
}

function tabHref(tab, extra = {}) {
  const query = new URLSearchParams({ tab, ...extra })
  return `#/admin?${query.toString()}`
}

function currentTab(route) {
  const tab = route?.query?.tab
  return TABS.some((entry) => entry.id === tab) ? tab : 'students'
}


// ---- 渲染骨架 -----------------------------------------------------------------

function buildTabs(activeTab) {
  return el('nav', { class: 'admin-tabs', 'aria-label': '管理页标签' }, TABS.map((entry) => el(
    'a',
    {
      class: entry.id === activeTab ? 'admin-tab admin-tab--active' : 'admin-tab',
      href: tabHref(entry.id),
      text: entry.label,
      'aria-current': entry.id === activeTab ? 'page' : null,
    },
  )))
}

function buildHeader(subtitle) {
  return el('header', { class: 'page__header' }, [
    el('div', { class: 'page__titles' }, [
      el('h1', { class: 'page__title', text: '管理' }),
      el('p', { class: 'page__subtitle', text: subtitle }),
    ]),
  ])
}

function draw() {
  if (!mainEl || !state) return
  const nodes = [buildHeader('教师侧 · 学生数据 / 账号管理 / 审计（数据逐人，不产出跨学生派生指标）'), buildTabs(state.tab)]
  if (state.error) nodes.push(notice({ message: state.error, tone: 'danger' }))
  if (state.flash) nodes.push(notice({ message: state.flash, tone: 'success' }))
  for (const block of state.blocks) nodes.push(block)
  mount(mainEl, el('section', { class: 'page admin-page', dataset: { block: 'admin-shell' } }, nodes))
}

function buildLoading() {
  return el('section', { class: 'page admin-page' }, [
    buildHeader('正在读取管理数据…'),
    card({ title: '管理数据', body: skeleton({ lines: 5 }) }),
  ])
}

// ---- ① 学生数据（数据视图）----------------------------------------------------

function buildStudentDetail(row) {
  const mastery = table({
    columns: [
      { key: 'topic', label: '知识点' },
      { key: 'attempts', label: '作答' },
      { key: 'correct', label: '正确' },
      { key: 'updated_at', label: '更新于', render: (entry) => fmtTime(entry.updated_at) },
    ],
    rows: row.mastery,
    empty: '这个学生还没有可累计的掌握度',
  })
  const recent = table({
    columns: [
      { key: 'created_at', label: '时间', render: (entry) => fmtTime(entry.created_at) },
      { key: 'item_id', label: '题目' },
      { key: 'correct', label: '判定', render: (entry) => (entry.correct === null ? '不可判定' : entry.correct ? '正确' : '错误') },
      { key: 'hint_used', label: '提示次数' },
      { key: 'source', label: '来源', render: (entry) => (entry.source === 'wrong_review' ? '错题重做' : '练习') },
    ],
    rows: row.recent_attempts,
    empty: '最近没有作答记录',
  })
  return card({
    title: `${row.display_name || row.username} · ${shortId(row.user_id)}`,
    body: el('div', { class: 'admin-detail' }, [
      el('p', { class: 'admin-muted', text: `作答 ${row.attempt_count} 条 · 正确 ${row.correct_count} 条 · 建号 ${fmtTime(row.created_at)}` }),
      mastery,
      recent,
    ]),
  })
}

function buildStudentsTab() {
  const rows = state.students
  const body = table({
    columns: [
      {
        key: 'display_name',
        label: '学生',
        render: (row) => el('div', {}, [
          el('span', { text: row.display_name || row.username }),
          el('span', { class: 'admin-muted', text: ` · ${shortId(row.user_id)}` }),
        ]),
      },
      { key: 'attempt_count', label: '作答' },
      { key: 'correct_count', label: '正确' },
      { key: 'topics', label: '知识点', render: (row) => String(row.mastery.length) },
      {
        key: 'view',
        label: '操作',
        render: (row) => el('a', {
          class: 'admin-link',
          href: tabHref('students', { user: row.user_id }),
          text: state.selected && state.selected.user_id === row.user_id ? '查看中' : '查看',
        }),
      },
    ],
    rows,
    empty: '还没有学生账号 —— 去「账号管理」标签导入',
  })
  return [card({ title: `学生（${rows.length}）`, body }), state.selected ? buildStudentDetail(state.selected) : null]
}



// ---- ② 账号管理 ---------------------------------------------------------------

let lastRoute = { query: {} }
let pendingFlash = ''

async function refresh() {
  state.flash = pendingFlash
  pendingFlash = ''
  await load(state.tab, lastRoute)
  draw()
}

async function toggleStatus(row) {
  const next = row.status === 'disabled' ? 'active' : 'disabled'
  try {
    await patch(`/api/admin/users/${row.user_id}`, { status: next }, { silent: true })
    pendingFlash = `已${next === 'active' ? '启用' : '停用'} ${row.username}`
    await refresh()
  } catch (error) {
    state.error = error?.message ?? '操作失败'
    draw()
  }
}

async function removeUser(row) {
  const ok = window.confirm(`确定删除 ${row.username}？数据会被硬删、账号会脱敏，且不可恢复。`)
  if (!ok) return
  try {
    const result = await del(`/api/admin/users/${row.user_id}`, { silent: true })
    pendingFlash = `已删除 ${row.username}（读回：作答 ${result.deleted_rows.attempt} 行 · 保留审计 ${result.audit_rows_retained} 条）`
    await refresh()
  } catch (error) {
    state.error = error?.message ?? '删除失败'
    draw()
  }
}

function buildUsersTable() {
  return table({
    columns: [
      {
        key: 'username',
        label: '账号',
        render: (row) => el('div', {}, [
          el('span', { text: row.username }),
          el('span', { class: 'admin-muted', text: ` · ${shortId(row.user_id)} · ${row.role === 'admin' ? '管理员' : '学生'}` }),
        ]),
      },
      { key: 'status', label: '状态', render: (row) => statusLabel(row.status) },
      { key: 'failed_count', label: '失败' },
      { key: 'active_tokens', label: '活跃令牌' },
      { key: 'last_login_at', label: '最近登录', render: (row) => fmtTime(row.last_login_at) },
      {
        key: 'actions',
        label: '操作',
        render: (row) => el('div', { class: 'admin-actions' }, [
          el('button', {
            class: 'button',
            type: 'button',
            text: row.status === 'disabled' ? '启用' : '停用',
            onClick: () => toggleStatus(row),
          }),
          row.status === 'deleted' ? null : el('button', {
            class: 'button button--danger',
            type: 'button',
            text: '删除',
            onClick: () => removeUser(row),
          }),
        ].filter(Boolean)),
      },
    ],
    rows: state.users,
    empty: '还没有账号 —— 用下面的导入表单建第一批学生',
  })
}



function buildResetCard() {
  const options = state.users.filter((row) => row.status !== 'deleted')
  const select = el('select', { class: 'admin-input', name: 'user_id' }, options.map((row) => el(
    'option', { value: row.user_id, text: `${row.username} · ${shortId(row.user_id)}` },
  )))
  const password = el('input', {
    class: 'admin-input',
    type: 'password',
    name: 'password',
    placeholder: '临时密码（学生下次登录必须改）',
    autocomplete: 'new-password',
    maxlength: '72',
  })
  const form = el('form', { class: 'admin-form' }, [
    select,
    password,
    el('button', { class: 'button button--primary', type: 'submit', text: '重置密码' }),
  ])
  form.addEventListener('submit', async (event) => {
    event.preventDefault()
    if (!password.value || !select.value) return
    try {
      const result = await patch(
        `/api/admin/users/${select.value}`, { password: password.value }, { silent: true },
      )
      password.value = ''
      pendingFlash = `已重置密码：强制改密 + 撤销 ${result.tokens_revoked} 条令牌（请经单独渠道告知学生）`
      await refresh()
    } catch (error) {
      state.error = error?.message ?? '重置失败'
      draw()
    }
  })
  return card({ title: '重置密码（强制下次登录改密 + 撤销全部令牌）', body: form })
}

function parseImportRows(raw) {
  return raw.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => {
    const [username, password, ...rest] = line.split(/\s+/)
    const row = { username, password }
    if (rest.length) row.display_name = rest.join(' ')
    return row
  })
}

function buildImportCard() {
  const textarea = el('textarea', {
    class: 'admin-input admin-textarea',
    rows: '4',
    placeholder: '每行一个学生：用户名 初始密码 [显示名]',
    'aria-label': '批量导入学生',
  })
  const form = el('form', { class: 'admin-form' }, [
    textarea,
    el('button', { class: 'button button--primary', type: 'submit', text: '批量导入（只能建学生）' }),
  ])
  form.addEventListener('submit', async (event) => {
    event.preventDefault()
    const rows = parseImportRows(textarea.value)
    if (!rows.length) return
    try {
      const result = await post('/api/admin/users/import', { rows }, { silent: true })
      const rejected = result.rejected.map((item) => `第 ${item.row + 1} 行：${item.reason}`).join('；')
      pendingFlash = `导入完成：新建 ${result.created} 个学生${rejected ? ` · 拒绝 ${result.rejected.length} 行（${rejected}）` : ''}`
      textarea.value = ''
      await refresh()
    } catch (error) {
      state.error = error?.message ?? '导入失败'
      draw()
    }
  })
  return card({ title: '批量导入（角色固定 student · 首次登录必须改密）', body: form })
}

function buildUsersTab() {
  return [
    card({ title: `账号（${state.users.length}）`, body: buildUsersTable() }),
    buildResetCard(),
    buildImportCard(),
  ]
}



// ---- ③ 审计 -------------------------------------------------------------------

function buildAuditTab() {
  const filter = el('select', { class: 'admin-input', 'aria-label': '按动作筛选' }, [
    el('option', { value: '', text: '全部动作' }),
    ...AUDIT_ACTIONS.map((action) => el('option', {
      value: action,
      text: action,
      selected: state.auditAction === action ? 'selected' : null,
    })),
  ])
  filter.addEventListener('change', () => {
    navigate(tabHref('audit', filter.value ? { action: filter.value } : {}))
  })
  const rows = table({
    columns: [
      { key: 'created_at', label: '时间', render: (row) => fmtTime(row.created_at) },
      { key: 'action', label: '动作' },
      { key: 'actor', label: '操作者', render: (row) => `${row.actor_role} · ${shortId(row.actor_id)}` },
      { key: 'target', label: '对象', render: (row) => (row.target_id ? shortId(row.target_id) : '—') },
      { key: 'detail', label: '详情', render: (row) => JSON.stringify(row.detail ?? {}) },
    ],
    rows: state.audit,
    empty: '还没有审计记录',
  })
  return [
    card({ title: '审计（谁在何时看了 / 改了谁的数据）', body: filter }),
    card({ title: `记录（${state.audit.length}）`, body: rows }),
  ]
}

// ---- ④ 数据加载与路由 ---------------------------------------------------------

async function load(tab, route) {
  state.tab = tab
  const blocks = []
  if (tab === 'students') {
    const query = new URLSearchParams({ limit: String(PAGE_LIMIT) })
    if (route.query.user) query.set('user', route.query.user)
    const page = await get(`/api/admin/students?${query.toString()}`, { silent: true })
    state.students = Array.isArray(page?.items) ? page.items : []
    state.selected = route.query.user
      ? state.students.find((row) => row.user_id === route.query.user) ?? null
      : null
    blocks.push(...buildStudentsTab())
  } else if (tab === 'users') {
    const page = await get(`/api/admin/users?limit=${PAGE_LIMIT}`, { silent: true })
    state.users = Array.isArray(page?.items) ? page.items : []
    blocks.push(...buildUsersTab())
  } else {
    const query = new URLSearchParams({ limit: String(PAGE_LIMIT) })
    if (route.query.action) query.set('action', route.query.action)
    const page = await get(`/api/admin/audit?${query.toString()}`, { silent: true })
    state.audit = Array.isArray(page?.items) ? page.items : []
    state.auditAction = route.query.action ?? ''
    blocks.push(...buildAuditTab())
  }
  state.blocks = blocks.filter(Boolean)
}

function blankState(tab) {
  return {
    tab,
    error: '',
    flash: '',
    blocks: [],
    students: [],
    users: [],
    audit: [],
    auditAction: '',
    selected: null,
  }
}

export async function renderAdmin(route = { query: {} }) {
  const main = qs('#app-main')
  if (!main) return
  const seq = ++renderSeq
  mainEl = main
  lastRoute = route
  mount(main, buildLoading())
  state = blankState(currentTab(route))

  // ★ 刷新后 `role()` 是 null（当前用户不落 localStorage）⇒ 先问服务端一次，不猜。
  if (!role() && token()) {
    try {
      setUser(await get('/api/me', { silent: true }))
    } catch {
      /* 401 已由 core/api.js 清会话；下面按未登录处理 */
    }
  }
  if (!token()) {
    state.error = '请先登录再访问管理页（#/login）'
    draw()
    return
  }
  if (role() !== 'admin') {
    state.error = '无权访问：管理页只对管理员开放（403 forbidden）'
    draw()
    return
  }

  state.flash = pendingFlash
  pendingFlash = ''
  try {
    await load(state.tab, route)
  } catch (error) {
    state.error = `管理数据加载失败：${error?.message ?? '未知错误'}`
    state.blocks = []
  }
  if (seq !== renderSeq) return
  draw()
}

register('#/admin', renderAdmin)


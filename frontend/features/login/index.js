/**
 * 登录页（`#/login`）—— ★ **域1 的页面**（一人一页，`docs/05 §4`）：W1 ④。
 * ============================================================================
 * 判据（全部取自权威，不在本文件自造口径）：
 *   - `docs/02 §7.1`：`#/login` 必须**可寻址**（刷新、分享、回退都成立）；
 *   - `docs/02 §7.4`：`core/auth.js` 的**三个键名不动**（本页只经 `setSession` / `clearSession`，
 *     既不知道也不写 `dsh.*` 字面量）；
 *   - `docs/02 §6.3` 的**前端行为列**：`bad_credentials` **不清空用户名** ·
 *     `must_change_pw` **强制跳设置页且不清令牌** · `account_disabled` / `locked` 各自一句话；
 *   - `docs/06 §11` 的 W1 行：登录 → 令牌 → `GET /api/me` → 越权 404 → 两把锁；
 *   - R-E：**不用 `innerHTML`**（本页只用 `core/dom.js` 的 `el` / `setText`）；
 *   - R-D：颜色 / 圆角 / 间距**只取 `tokens.css`**（见 `features/login/styles.css`）。
 *
 * ★ **本页不造"假登录成功"**：`POST /api/auth/login` 是 W1 ①（域2）的交付，它落地前
 *   本页如实显示"接口尚未实现"，而不是假装登录过（`docs/02 §4.2.1` 的"不留产品级 mock"
 *   与 N2 诚实）。契约 `user.schema.json#/definitions/loginRequest|session` 只用来对齐字段名。
 */
import { el, mount, qs } from '../../core/dom.js';
import { register, navigate } from '../../core/router.js';
import { post } from '../../core/api.js';
import { clearSession, role, setSession, token, user } from '../../core/auth.js';
import { card, notice } from '../../components/index.js';

if (!document.querySelector('link[data-login-style]')) {
  document.head.append(el('link', {
    rel: 'stylesheet',
    href: '/features/login/styles.css',
    dataset: { loginStyle: 'true' },
  }));
}

/** `docs/02 §6.3` 的四个 code → 一句话。★ 分支一律按 `code`，不按 `message`。 */
const CODE_MESSAGES = {
  bad_credentials: '用户名或密码不正确。',
  account_disabled: '账号已停用，请联系老师。',
  locked: '连续失败次数过多，账号已被临时锁定，请稍后再试。',
  must_change_pw: '请先设置新密码，再继续使用。',
};

/** 登录接口还没落地时的如实说明（`error.status === 404`）。 */
const NOT_IMPLEMENTED = '登录接口尚未实现（属 W1 · 域2）—— 服务未就绪。';

function field(id, label, type, options = {}) {
  return el('div', { class: 'login-field' }, [
    el('label', { class: 'login-field__label', for: id, text: label }),
    el('input', {
      class: 'login-field__input',
      id,
      name: id,
      type,
      autocomplete: options.autocomplete,
      required: true,
      maxlength: options.maxlength,
      disabled: options.disabled,
    }),
  ]);
}

function renderLoggedIn(main) {
  mount(main, el('section', { class: 'page login-page', dataset: { block: 'login-session' } }, [
    el('header', { class: 'page__header' }, [
      el('div', { class: 'page__titles' }, [
        el('h1', { class: 'page__title', text: '登录' }),
        el('p', { class: 'page__subtitle', text: '登录态是外壳的事（W1 ④）' }),
      ]),
    ]),
    card({
      title: '当前会话',
      body: el('div', { class: 'login-session' }, [
        notice({
          message: `已登录（角色：${role() ?? '待 /api/me 取回'}）。刷新后角色由 GET /api/me 重新取 —— 在它回来之前界面按"角色待取回"处理。`,
          tone: 'info',
        }),
        el('div', { class: 'login-actions' }, [
          el('a', { class: 'button button--primary', href: '#/home', text: '回首页' }),
          el('button', {
            class: 'button',
            type: 'button',
            text: '退出登录',
            onclick: () => {
              clearSession();   // 只清会话，不动模型凭据（docs/02 §1.4 第 3 条）
              renderLogin();
            },
          }),
        ]),
      ]),
    }),
  ]));
}

export function renderLogin() {
  const main = qs('#app-main');
  if (!main) return;
  if (token() && user()) {
    renderLoggedIn(main);
    return;
  }

  const errorBox = el('div', { class: 'login-error', role: 'alert', 'aria-live': 'polite' });
  const usernameInput = field('login-username', '用户名', 'text', { autocomplete: 'username', maxlength: 32 });
  const passwordInput = field('login-password', '密码', 'password', { autocomplete: 'current-password', maxlength: 72 });
  const username = qs('#login-username', usernameInput);
  const password = qs('#login-password', passwordInput);
  const submit = el('button', { class: 'button button--primary', type: 'submit', text: '登录' });

  const form = el('form', { class: 'login-form', novalidate: false }, [
    usernameInput,
    passwordInput,
    el('div', { class: 'login-actions' }, [submit]),
    el('p', { class: 'login-hint', text: '学生与教师（管理员）用同一个入口；管理员账号由 CLI 创建（F15：接口不可创建管理员）。' }),
  ]);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    mount(errorBox);                       // 清上一次的错误，避免过期提示留在屏幕上
    submit.disabled = true;
    submit.textContent = '登录中…';
    try {
      const session = await post('/api/auth/login', { username: username.value, password: password.value });
      setSession({ token: session?.token, user: session?.user ?? null });
      password.value = '';                 // ★ 只清密码；用户名保留在输入框里
      navigate('#/home');
    } catch (error) {
      // ★ 分支按 code（`docs/02 §6.3`）：`bad_credentials` 不清用户名，也不清令牌。
      const hint = error?.status === 404
        ? NOT_IMPLEMENTED
        : (CODE_MESSAGES[error?.code] ?? error?.message ?? '登录失败，请稍后再试。');
      mount(errorBox, notice({ message: hint, tone: 'danger' }));
      // ★ 令牌本身有效时**不得清**（`must_change_pw` 的原文：不清本地令牌）；
      //   `core/api.js` 只对 `401 unauthenticated` 清会话，这里不再重复判断。
      if (error?.code === 'must_change_pw') navigate('#/settings?tab=model');
    } finally {
      submit.disabled = false;
      submit.textContent = '登录';
    }
  });

  mount(main, el('section', { class: 'page login-page', dataset: { block: 'login' } }, [
    el('header', { class: 'page__header' }, [
      el('div', { class: 'page__titles' }, [
        el('h1', { class: 'page__title', text: '登录' }),
        el('p', { class: 'page__subtitle', text: '账号与令牌（F15 · N6）' }),
      ]),
    ]),
    card({ title: '账号登录', body: el('div', { class: 'login-body' }, [errorBox, form]) }),
  ]));
  username.focus();
}

register('#/login', renderLogin);

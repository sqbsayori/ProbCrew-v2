/**
 * 设置页（`#/settings`）—— N14 / N15 / N16 的承载页。
 *
 * 口径（不在本页自造）：
 *   - 供应商清单来自 `GET /api/model/providers`，**不在前端硬编码**（契约 `chat.schema.json`）；
 *   - 端点与 Key 只经 `core/auth.js` 的三个 `localStorage` 键读写（`docs/02 §7.4`）；
 *   - 告知文案取 `GET /api/me/notice` 的 `message`（`must_render`），本页只渲染不拼；
 *   - 自检走 `POST /api/model/ping`，与 `/api/chat` 共用服务端端点校验；
 *   - R-E：只用 `core/dom.js`；R-D：颜色等只取 `tokens.css`。
 */
import { el, mount, qs } from '../../core/dom.js';
import { register, parse, navigate } from '../../core/router.js';
import { get, post } from '../../core/api.js';
import { emit, EVENTS } from '../../core/bus.js';
import {
  clearModelKey,
  clearSession,
  endpoint,
  modelKey,
  setEndpoint,
  setModelKey,
  token,
} from '../../core/auth.js';
import { card, notice } from '../../components/index.js';

if (!document.querySelector('link[data-settings-style]')) {
  document.head.append(el('link', {
    rel: 'stylesheet',
    href: '/features/settings/styles.css',
    dataset: { settingsStyle: 'true' },
  }));
}

const LOOPBACK = /^http:\/\/(127\.0\.0\.1|localhost)(:[0-9]{1,5})?(\/[^\s]*)?$/;

let settingsSeq = 0;

function field(id, label, control, hint) {
  return el('div', { class: 'settings-field' }, [
    el('label', { class: 'settings-field__label', for: id, text: label }),
    control,
    hint ? el('p', { class: 'settings-field__hint', text: hint }) : null,
  ].filter(Boolean));
}

function input(id, type, value = '') {
  return el('input', {
    class: 'settings-field__input',
    id,
    name: id,
    type,
    value,
    autocomplete: type === 'password' ? 'off' : 'off',
  });
}

function statusBox() {
  return el('div', { class: 'settings-status', role: 'status', 'aria-live': 'polite' });
}

function showStatus(box, message, tone = 'info') {
  mount(box, notice({ message, tone }));
}

function buildGate(main, message) {
  mount(main, el('section', { class: 'page settings-page' }, [
    el('header', { class: 'page__header' }, [
      el('div', { class: 'page__titles' }, [
        el('h1', { class: 'page__title', text: '设置' }),
        el('p', { class: 'page__subtitle', text: '模型端点与凭据 · 外传告知' }),
      ]),
    ]),
    card({
      title: '需要登录',
      body: el('div', { class: 'settings-body' }, [
        notice({ message, tone: 'warn' }),
        el('a', { class: 'button button--primary', href: '#/login', text: '去登录' }),
      ]),
    }),
  ]));
}

function buildPage(main, providers, current, noticeStatus, box) {
  const providerSelect = el('select', { class: 'settings-field__input', id: 'settings-provider' },
    providers.map((item) => el('option', {
      value: item.provider,
      text: item.label,
      selected: current?.provider === item.provider,
    })));
  if (!current?.provider && providers.length) providerSelect.value = providers[0].provider;

  const providerHint = el('p', { class: 'settings-field__hint' });
  const defaultOf = (id) => providers.find((item) => item.provider === id)?.default_base_url ?? '';
  const initialProvider = providerSelect.value;
  const modelInput = input('settings-model', 'text', current?.model ?? '');
  const urlInput = input('settings-base-url', 'url', current?.base_url ?? defaultOf(initialProvider));
  const keyInput = input('settings-model-key', 'password');
  keyInput.placeholder = modelKey() ? '已保存；留空表示不修改' : '粘贴你的 API Key';
  const loopback = el('input', {
    id: 'settings-loopback',
    type: 'checkbox',
    checked: LOOPBACK.test(current?.base_url ?? ''),
  });
  const save = el('button', { class: 'button button--primary', type: 'submit', text: '保存配置' });
  const ping = el('button', { class: 'button', type: 'button', text: '保存并自检' });
  const clear = el('button', { class: 'button', type: 'button', text: '清除已保存凭据' });
  const oldPw = input('settings-old-pw', 'password');
  const newPw = input('settings-new-pw', 'password');
  const confirmPw = input('settings-confirm-pw', 'password');
  const changePw = el('button', { class: 'button button--primary', type: 'submit', text: '修改密码' });
  const securityBox = statusBox();

  function syncProviderHint() {
    const selected = providers.find((item) => item.provider === providerSelect.value);
    providerHint.textContent = selected
      ? `${selected.requires_key ? '该供应商需要 Key' : '该端点可不需要 Key'}${selected.default_base_url ? ` · 默认地址 ${selected.default_base_url}` : ''}`
      : '';
  }
  syncProviderHint();

  providerSelect.addEventListener('change', () => {
    const next = defaultOf(providerSelect.value);
    if (next && (!urlInput.value || urlInput.value === defaultOf(initialProvider))) urlInput.value = next;
    syncProviderHint();
  });

  function collect() {
    const provider = providerSelect.value;
    const model = modelInput.value.trim();
    const baseUrl = urlInput.value.trim();
    if (!provider || !model || !baseUrl) throw new Error('供应商、模型名与端点地址都必须填写。');
    if (!/^https:\/\//.test(baseUrl) && !(loopback.checked && LOOPBACK.test(baseUrl))) {
      throw new Error('非本机端点必须使用 https；本机端点需勾选"允许本机端点"并使用 127.0.0.1 或 localhost 的地址。');
    }
    return { provider, model, base_url: baseUrl };
  }

  function persist() {
    const config = collect();
    setEndpoint(config);
    const typed = keyInput.value;
    if (typed) {
      setModelKey(typed);
      keyInput.value = '';
      keyInput.placeholder = '已保存；留空表示不修改';
    }
    const selected = providers.find((item) => item.provider === config.provider);
    if (!modelKey() && selected?.requires_key) {
      showStatus(box, '端点已保存，但还没有保存 Key —— 自检与答疑都会返回 model_key_missing。', 'warn');
      return false;
    }
    showStatus(box, '配置已保存到本机浏览器（Key 不会写进聊天记录或日志）。', 'success');
    return true;
  }

  const form = el('form', { class: 'settings-form' }, [
    field('settings-provider', '供应商', providerSelect, ''),
    providerHint,
    field('settings-model', '模型名', modelInput, '例如 deepseek-chat；会拼进上游请求体。'),
    field('settings-base-url', '端点地址', urlInput, '远程端点必须 https；本机端点仅单机部署可用。'),
    el('label', { class: 'settings-check', for: 'settings-loopback' }, [
      loopback,
      el('span', { text: '允许本机端点（仅 PROBCREW_DEPLOYMENT=single 时服务端放行）' }),
    ]),
    field('settings-model-key', '模型凭据（X-Model-Key）', keyInput, '只保存在本机浏览器，随请求头发送；不落库、不进日志。'),
    el('div', { class: 'settings-actions' }, [save, ping, clear]),
  ]);

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    try { persist(); } catch (error) { showStatus(box, error.message, 'danger'); }
  });

  ping.addEventListener('click', async () => {
    let ok = false;
    try { ok = persist(); } catch (error) { showStatus(box, error.message, 'danger'); return; }
    if (!ok) return;
    ping.disabled = true;
    try {
      const result = await post('/api/model/ping', endpoint(), { modelKey: true });
      if (result?.ok) {
        showStatus(box, `自检通过：${Math.round(result.latency_ms)} ms。`, 'success');
      } else {
        showStatus(box, result?.message || '自检未通过：端点没有返回可用回答。', 'danger');
      }
    } catch (error) {
      showStatus(box, `自检失败：${error?.message || '未知错误'}`, 'danger');
    } finally {
      ping.disabled = false;
    }
  });

  clear.addEventListener('click', () => {
    clearModelKey();
    keyInput.value = '';
    keyInput.placeholder = '粘贴你的 API Key';
    showStatus(box, '已清除本机保存的模型凭据。', 'info');
  });

  const securityForm = el('form', { class: 'settings-form' }, [
    field('settings-old-pw', '当前密码', oldPw, ''),
    field('settings-new-pw', '新密码', newPw, '1–72 个字符；修改后所有设备都需要重新登录。'),
    field('settings-confirm-pw', '确认新密码', confirmPw, ''),
    el('div', { class: 'settings-actions' }, [changePw]),
  ]);
  securityForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!oldPw.value || !newPw.value) {
      showStatus(securityBox, '当前密码与新密码都必须填写。', 'warn');
      return;
    }
    if (newPw.value !== confirmPw.value) {
      showStatus(securityBox, '两次输入的新密码不一致。', 'danger');
      return;
    }
    changePw.disabled = true;
    try {
      await post('/api/auth/password', {
        old_password: oldPw.value,
        new_password: newPw.value,
      });
      // 改密成功 ⇒ 服务端撤销该用户全部令牌（含本次）⇒ 必须重新登录。
      clearSession();
      window.addEventListener('hashchange', () => {
        emit(EVENTS.NOTICE, { message: '密码已修改，请用新密码重新登录。', tone: 'success' });
      }, { once: true });
      navigate('#/login');
    } catch (error) {
      const message = error?.code === 'bad_credentials'
        ? '当前密码不正确。'
        : `修改失败：${error?.message || '未知错误'}`;
      showStatus(securityBox, message, 'danger');
    } finally {
      changePw.disabled = false;
    }
  });

  const egress = noticeStatus?.egress === 'remote'
    ? `远程（${noticeStatus.provider || '未知供应商'} / ${noticeStatus.model || '未知模型'}）`
    : noticeStatus?.egress === 'loopback' ? '本机端点（不发往第三方）' : '尚无问答记录';

  const noticeBox = el('div', { class: 'settings-notice' });
  const ack = el('button', { class: 'button', type: 'button', text: '我已知悉' });
  ack.disabled = !noticeStatus?.ask;
  ack.addEventListener('click', async () => {
    ack.disabled = true;
    try {
      const next = await post('/api/me/notice', { ack: true });
      mount(noticeBox, notice({ message: next.message, tone: 'info' }));
      ack.disabled = true;
      showStatus(box, '已记录告知确认。', 'success');
    } catch (error) {
      ack.disabled = false;
      showStatus(box, `告知确认失败：${error?.message || '未知错误'}`, 'danger');
    }
  });
  mount(noticeBox, notice({
    message: noticeStatus?.message || '告知状态暂不可用。',
    tone: noticeStatus?.egress === 'remote' ? 'warn' : 'info',
  }));

  mount(main, el('section', { class: 'page settings-page', dataset: { block: 'settings' } }, [
    el('header', { class: 'page__header' }, [
      el('div', { class: 'page__titles' }, [
        el('h1', { class: 'page__title', text: '设置' }),
        el('p', { class: 'page__subtitle', text: '模型端点与凭据 · 外传告知（N14 · N15 · N16）' }),
      ]),
    ]),
    el('div', { class: 'settings-grid' }, [
      card({ title: '模型端点', body: el('div', { class: 'settings-body' }, [form, box]) }),
      card({
        title: '数据去向与告知',
        body: el('div', { class: 'settings-body' }, [
          el('p', { class: 'settings-meta' }, [
            el('span', { class: 'settings-meta__label', text: '最近一次实际去向：' }),
            el('strong', { text: egress }),
          ]),
          el('p', { class: 'settings-meta' }, [
            el('span', { class: 'settings-meta__label', text: '载荷范围：' }),
            el('span', { text: (noticeStatus?.payload_fields || []).join('、') || '—' }),
          ]),
          noticeBox,
          el('div', { class: 'settings-actions' }, [ack]),
          el('p', { class: 'settings-field__hint', text: '告知确认是每账号一次；换设备仍会再次提示。' }),
        ]),
      }),
      card({
        title: '账号安全',
        body: el('div', { class: 'settings-body' }, [
          notice({ message: '修改密码会撤销本账号在所有设备上的登录令牌，需要重新登录。', tone: 'info' }),
          securityForm,
          securityBox,
        ]),
      }),
    ]),
  ]));
}

export async function renderSettings(route = parse()) {
  const main = qs('#app-main');
  if (!main) return;
  const seq = ++settingsSeq;
  if (!token()) {
    buildGate(main, '设置模型端点与凭据需要先登录。');
    return;
  }
  const box = statusBox();
  try {
    const [providerBody, noticeStatus] = await Promise.all([
      get('/api/model/providers'),
      get('/api/me/notice', { silent: true }).catch(() => null),
    ]);
    const providers = Array.isArray(providerBody?.providers) ? providerBody.providers : [];
    if (!providers.length) throw new Error('供应商清单为空');
    if (seq !== settingsSeq) return;
    buildPage(main, providers, endpoint(), noticeStatus, box);
    if (route.query?.tab === 'notice') {
      const target = qs('.settings-grid > :last-child', main);
      if (target) target.scrollIntoView({ block: 'start' });
    }
  } catch (error) {
    if (seq !== settingsSeq) return;
    mount(main, el('section', { class: 'page settings-page' }, [
      el('header', { class: 'page__header' }, [
        el('div', { class: 'page__titles' }, [
          el('h1', { class: 'page__title', text: '设置' }),
          el('p', { class: 'page__subtitle', text: '模型端点与凭据 · 外传告知' }),
        ]),
      ]),
      notice({ message: `设置页读取失败：${error?.message || '未知错误'}`, tone: 'danger' }),
    ]));
  }
}

register('#/settings', renderSettings);

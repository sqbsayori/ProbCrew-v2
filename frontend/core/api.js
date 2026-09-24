/**
 * `core/api.js` —— fetch 包装 + **统一错误体解析**（`docs/03 §5.1`）。
 *
 * 三条纪律：
 * ① **一律相对 `/api/*`** —— 前后端同源（`docs/03 §8.4`），不写死后端地址（校验⑦ / N18②）；
 * ② **错误体按 `contracts/error.schema.json` 解析** —— 形状是 `{ error: { code, message, … } }`，
 *    解析结果进 `ApiError` 并广播 `api:error`；
 * ③ ★ **401 `unauthenticated` ⇒ 清本地令牌**（`docs/02 §6.3` 的前端行为列）；
 *    **"跳登录"不在这里做** —— 那是外壳对 `auth:changed` 的反应（一处决定，别处不重复）。
 */

import { emit, EVENTS } from './bus.js'
import { clearSession, modelKey, token } from './auth.js'

/** 统一错误体的解析结果。 */
export class ApiError extends Error {
  /**
   * @param {{ status: number, code: string, message: string, detail?: any, traceId?: string|null }} init
   */
  constructor({ status, code, message, detail = null, traceId = null }) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
    this.traceId = traceId
  }
}

/** 把响应体解析成统一错误体（解不出来时给一个诚实的兜底，不假装知道 code）。 */
function toApiError(response, payload) {
  const envelope = payload && typeof payload === 'object' ? payload.error : null
  return new ApiError({
    status: response.status,
    code: typeof envelope?.code === 'string' ? envelope.code : 'internal_error',
    message: typeof envelope?.message === 'string' ? envelope.message : `HTTP ${response.status}`,
    detail: envelope?.detail ?? null,
    traceId: envelope?.trace_id ?? payload?.trace_id ?? null,
  })
}

/**
 * 发一个请求。
 *
 * @param {string} method
 * @param {string} path **必须以 `/api/` 开头**（相对路径）
 * @param {{
 *   body?: any, headers?: Record<string, string>, signal?: AbortSignal,
 *   auth?: boolean, modelKey?: boolean, raw?: boolean,
 * }} [options]
 *   - `auth`（缺省 `true`）：带上 `Authorization: Bearer <token>`
 *   - `modelKey`（缺省 `false`）：带上 `X-Model-Key`（只有需要模型的端点才带）
 *   - `raw`（缺省 `false`）：不做 JSON 解析，直接给 `Response`
 * @returns {Promise<any>}
 */
export async function request(method, path, options = {}) {
  const { body, headers = {}, signal, auth = true, modelKey: withModelKey = false, raw = false } = options
  if (!String(path).startsWith('/api/')) {
    throw new TypeError(`api.${method.toLowerCase()}(${path}): 路径必须是相对 /api/*（docs/03 §8.4）`)
  }

  const finalHeaders = { Accept: 'application/json', ...headers }
  if (auth) {
    const current = token()
    if (current) finalHeaders.Authorization = `Bearer ${current}`
  }
  if (withModelKey) finalHeaders['X-Model-Key'] = modelKey() ?? ''

  let payload
  if (body !== undefined && body !== null) {
    if (body instanceof FormData) payload = body
    else {
      payload = JSON.stringify(body)
      finalHeaders['Content-Type'] ??= 'application/json'
    }
  }

  const response = await fetch(path, { method, headers: finalHeaders, body: payload, signal })
  if (raw) return response

  const contentType = response.headers.get('content-type') ?? ''
  let parsed = null
  if (response.status !== 204) {
    parsed = contentType.includes('application/json')
      ? await response.json().catch(() => null)
      : await response.text()
  }

  if (!response.ok) {
    const error = toApiError(response, parsed)
    if (error.status === 401 && error.code === 'unauthenticated') clearSession() // ③
    emit(EVENTS.API_ERROR, error)
    throw error
  }
  return parsed
}

/** `GET /api/…` */
export const get = (path, options) => request('GET', path, options)
/** `POST /api/…` */
export const post = (path, body, options) => request('POST', path, { ...options, body })
/** `PUT /api/…` */
export const put = (path, body, options) => request('PUT', path, { ...options, body })
/** `PATCH /api/…` */
export const patch = (path, body, options) => request('PATCH', path, { ...options, body })
/** `DELETE /api/…` */
export const del = (path, options) => request('DELETE', path, options)

// --------------------------------------------------------------------------- #
// 流式请求（答疑链路的 SSE 帧）—— `core/api.js` 的第二半
// --------------------------------------------------------------------------- #

/**
 * 按 SSE 读一条流，并把它解析成 `events.schema.json` 的帧对象。
 *
 * ★ **为什么必须是 `POST + fetch 流`**（`docs/02 §6.1` · `§4.2.1`）：凭据走请求头
 *   `X-Model-Key`，而 `EventSource` **不能带自定义头** ⇒ 改用它是**失败模式**
 *   （`docs/02 §8-13d`：凭据会静默失效）。
 * ★ **线格式的唯一权威是契约**（`contracts/events.schema.json` 的 `x-frame-conventions`）：
 *   **一条事件一行 `data: <json>` + `\n\n` 结尾** · UTF-8（中文不转义）⇒ 本函数的解析按它写，不另立格式。
 *
 * 三条纪律（都在别处写死，本函数只执行）：
 *   ① **`seq` 只用于去重**（单调递增；服务端**不做重放缓冲**，断流＝重新发起 —— `docs/02 §4.2-1`）；
 *   ② **终帧必须显式**（`done` / `error`，`docs/02 §6.5-2`）—— 因此"流断了却没终帧"**必须报出来**，
 *      不能当成功：调用方据此决定是否重新发起（`docs/02 §8-11`）；
 *   ③ 帧**不广播到 `core/bus`** —— 一次答疑的流属于**发起它的那个页面**（`onFrame` 回调）；
 *      广播会让"另一个页面也在听同一场答疑"成为可能。
 *
 * ★ **未知 `type` 必须被静默忽略**（契约原文）：本函数**照发**给 `onFrame`，由页面忽略它不认识的类型
 *   —— 那是"只增不改"能成立的前提。
 * ★ **`signal` 请务必传**：客户端断开 ⇒ 服务端**必须取消图**（`x-frame-conventions` 的
 *   `client_disconnect`）；不传就只能靠超时，会把单 worker 拖住。
 *
 * @param {string} path 必须以 `/api/` 开头
 * @param {any} body
 * @param {{
 *   onFrame?: (frame: {seq: number, type: string, ts: number, payload: any}) => void,
 *   signal?: AbortSignal,
 *   modelKey?: boolean,   // 缺省 true：答疑是唯一需要凭据的链路
 * }} [options]
 * @returns {Promise<{frames: number, lastSeq: number, endType: 'done'|'error'}>}
 * @throws {ApiError} 服务端以非 2xx 开头（含统一错误体；401 `unauthenticated` 会清会话）
 * @throws {Error} 流结束但**没有终帧**（客户端侧的中断，**不借用** `error.schema.json` 的 `code` 词汇表）
 */
export async function stream(path, body, options = {}) {
  const { onFrame, signal, modelKey: withModelKey = true } = options
  const response = await request('POST', path, {
    body,
    signal,
    raw: true,                                  // ① 先拿原始响应，才能读 body 流
    modelKey: withModelKey,
    headers: { Accept: 'text/event-stream' },   // ② 与 sse 端点显式协商
  })

  if (!response.ok) {
    // `raw` 绕过了 request 的错误处理 ⇒ 这里补上，行为与普通请求**逐条一致**。
    const text = await response.text().catch(() => '')
    let parsed = null
    try { parsed = text ? JSON.parse(text) : null } catch { parsed = null }
    const error = toApiError(response, parsed)
    if (error.status === 401 && error.code === 'unauthenticated') clearSession()
    emit(EVENTS.API_ERROR, error)
    throw error
  }
  if (!response.body) throw new Error('api.stream: 响应没有 body —— 该端点没有走流式返回')

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let lastSeq = 0
  let frames = 0
  let endType = null

  const handleLine = (line) => {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith(':')) return        // SSE 注释 / 心跳
    if (!trimmed.startsWith('data:')) return               // 其余字段（event: / id: / retry:）本仓不使用
    const json = trimmed.slice(5).trim()
    if (!json) return
    let frame = null
    try { frame = JSON.parse(json) } catch { return }      // 不是帧的行直接跳过（宁可少渲染，不静默成功）
    if (typeof frame?.seq === 'number') {
      if (frame.seq <= lastSeq) return                     // ★ ① 去重
      lastSeq = frame.seq
    }
    frames += 1
    if (frame?.type === 'done' || frame?.type === 'error') endType = frame.type
    if (typeof onFrame === 'function') onFrame(frame)      // ★ 未知 type 交给页面静默忽略
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const chunk = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      for (const line of chunk.split('\n')) handleLine(line)
      boundary = buffer.indexOf('\n\n')
    }
  }
  for (const line of buffer.split('\n')) handleLine(line)

  if (!endType) {
    // ★ ② 断流而**没有终帧** —— 这正是"终帧必须显式"要防的形态：不报出来，
    //   界面就会停在半截进度条上，而用户不知道是"跑完了"还是"网断了"。
    throw new Error('答疑流中断：未收到终帧（done / error）—— 按 docs/02 §8-11 需要重新发起')
  }
  return { frames, lastSeq, endType }
}


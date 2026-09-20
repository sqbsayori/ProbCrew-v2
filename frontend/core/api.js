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

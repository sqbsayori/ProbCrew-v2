/**
 * HITL 与答疑可用性的纯函数。
 *
 * 口径来自 `contracts/events.schema.json` 与 `docs/06 §15.4 C1/C3.4`：
 * - 按钮集合只能来自服务端 `hitl.request.payload.options`；
 * - `continue_uncertain` 可携带人工改稿 `final`；
 * - 超时、取消、继续都必须有明确的人话状态；
 * - 无模型端点或缺 Key 时，只关闭答疑，不关闭刷题/错题/知识库。
 */

const LOOPBACK_ENDPOINT = /^http:\/\/(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?(?:\/[^\s]*)?$/i

const ACTION_LABELS = Object.freeze({
  continue_uncertain: '接受草稿并继续',
  cancel: '取消人工确认',
  timeout: '等待超时',
})

const STATUS_LABELS = Object.freeze({
  idle: '尚未进入人工确认',
  pending: '挂起，等待人工确认',
  submitting: '已提交，等待服务端确认',
  continued: '已继续',
  cancelled: '已取消',
  timed_out: '已超时',
  resolved: '已处理',
  aborted: '已停止答疑',
})

/** 服务端 options → 去重、去空、限长的按钮动作；不新增选项。 */
export function normalizeHitlOptions(options) {
  if (!Array.isArray(options)) return []
  const seen = new Set()
  const result = []
  for (const value of options) {
    if (typeof value !== 'string') continue
    const action = value.trim()
    if (!action || action.length > 64 || seen.has(action)) continue
    seen.add(action)
    result.push(action)
  }
  return result
}

/** 已知动作给人话标签；未知动作仍按服务端原值渲染，不丢按钮。 */
export function hitlActionLabel(action) {
  const key = String(action || '')
  return ACTION_LABELS[key] || `服务端动作：${key || '未命名'}`
}

export function hitlStatusLabel(status) {
  return STATUS_LABELS[status] || STATUS_LABELS.resolved
}

/** `hitl.resolved` 的可见说明。 */
export function hitlResolutionMessage(payload = {}) {
  const action = String(payload.action || '')
  if (action === 'continue_uncertain') return '已接受人工确认，继续按确认后的草稿收口。'
  if (action === 'cancel') return '人工确认已取消：保留原草稿，本次结果仍标记为不确定。'
  if (action === 'timeout') return '人工确认等待超时：保留原草稿，本次结果未获人工确认。'
  return `人工确认已完成（${action || '未提供动作'}）。`
}

/** `done` 终帧的可见说明；不能把 uncertain 说成成功。 */
export function hitlDoneMessage(status, lastAction) {
  if (status !== 'uncertain') return '答疑完成。'
  if (lastAction === 'continue_uncertain') {
    return '答疑完成：已接受人工确认稿，但验证结果仍如实标记为不确定。'
  }
  if (lastAction === 'cancel') {
    return '答疑完成：人工确认已取消，未获人工确认；草稿已保留。'
  }
  if (lastAction === 'timeout') {
    return '答疑完成：人工确认超时，未获人工确认；草稿已保留。'
  }
  return '答疑完成：验证仍未通过，结果已如实标记为不确定。'
}

/**
 * 答疑是否具备发起条件。
 *
 * 本机 loopback 端点允许无 Key（单机形态由后端另行校验）；
 * 远端端点缺 Key 时必须在点击前明确不可用。
 */
export function qaAvailability(config, key) {
  const provider = typeof config?.provider === 'string' ? config.provider.trim() : ''
  const model = typeof config?.model === 'string' ? config.model.trim() : ''
  const baseUrl = typeof config?.base_url === 'string' ? config.base_url.trim() : ''
  if (!provider || !model || !baseUrl) {
    return {
      available: false,
      code: 'endpoint_incomplete',
      message: '模型端点未配置完整：刷题、错题本和知识库仍可使用；答疑暂不可用。请到设置页补齐 provider、model 与 base_url。',
    }
  }
  const hasCredential = typeof key === 'string' && key.trim().length > 0
  if (!hasCredential && !LOOPBACK_ENDPOINT.test(baseUrl)) {
    return {
      available: false,
      code: 'model_key_missing',
      message: '未配置模型凭据：刷题、错题本和知识库仍可使用；答疑暂不可用。请到设置页填写 Key。',
    }
  }
  return { available: true, code: '', message: '' }
}

/** 校验动作确实属于服务端 options，再构造契约请求体。 */
export function hitlRequestBody(action, final, options) {
  const normalized = normalizeHitlOptions(options)
  if (!normalized.includes(action)) {
    throw new TypeError('人工确认动作不在服务端 options 内')
  }
  const body = { action }
  if (action === 'continue_uncertain') {
    const value = String(final ?? '').trim()
    if (value) body.final = value.slice(0, 8000)
  }
  return body
}
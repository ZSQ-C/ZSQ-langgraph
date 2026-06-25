/**
 * API 客户端 - Axios 封装 + JWT Token 管理
 *
 * 功能：
 * - 自动附加 JWT Token
 * - 请求/响应拦截器
 * - 登录、会话、对话、文档、管理后台 API
 */
import axios from 'axios'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

// 请求拦截器：自动附加 JWT Token
http.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// 响应拦截器：401 自动跳转登录
http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
      localStorage.removeItem('user_info')
      window.location.href = '/'
    }
    return Promise.reject(error)
  }
)

// ============================================================
// 认证 API
// ============================================================

export function login(username, password) {
  return http.post('/auth/login', { username, password })
}

export function refreshToken(refreshToken) {
  return http.post('/auth/refresh', { refresh_token: refreshToken })
}

export function getCurrentUser() {
  return http.get('/auth/me')
}

// ============================================================
// 会话 API
// ============================================================

export function createSession(title = '新对话') {
  return http.post('/sessions/', { title })
}

export function listSessions(page = 1, pageSize = 20) {
  return http.get('/sessions/', { params: { page, page_size: pageSize } })
}

export function deleteSession(sessionId) {
  return http.delete(`/sessions/${sessionId}`)
}

// ============================================================
// 对话 API
// ============================================================

/**
 * 发送消息（SSE 流式）
 *
 * @param {string} sessionId - 会话ID
 * @param {string} message - 用户消息
 * @param {object} callbacks - 回调: { onEvent, onError, onComplete }
 * @returns {EventSource} - 可手动关闭
 */
export function sendMessage(sessionId, message, callbacks = {}) {
  const token = localStorage.getItem('access_token')
  const params = new URLSearchParams({ token })
  const url = `/api/chat/${sessionId}?${params.toString()}`

  // SSE 不能通过 axios（无法设置自定义 headers），使用 fetch + ReadableStream
  const controller = new AbortController()

  fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify({ message }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const errText = await response.text()
        const err = new Error(`HTTP ${response.status}: ${errText}`)
        if (callbacks.onError) callbacks.onError(err)
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        let currentEvent = 'message'
        let currentData = ''

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6)
          } else if (line === '' && currentData) {
            try {
              const parsed = JSON.parse(currentData)
              if (callbacks.onEvent) {
                callbacks.onEvent(currentEvent, parsed)
              }
            } catch (e) {
              // 非 JSON 数据作为纯文本流
              if (callbacks.onEvent) {
                callbacks.onEvent(currentEvent, { content: currentData })
              }
            }
            currentEvent = 'message'
            currentData = ''
          }
        }
      }

      if (callbacks.onComplete) callbacks.onComplete()
    })
    .catch((err) => {
      if (err.name !== 'AbortError' && callbacks.onError) {
        callbacks.onError(err)
      }
    })

  return controller
}

export function getMessages(sessionId) {
  return http.get(`/chat/${sessionId}/messages`)
}

export function approveOperation(sessionId, comment = '') {
  return http.post(`/chat/${sessionId}/approve`, { approved: true, comment })
}

export function rejectOperation(sessionId, reason = '') {
  return http.post(`/chat/${sessionId}/reject`, { reason })
}

// ============================================================
// 文档 API
// ============================================================

export function uploadDocument(file) {
  const formData = new FormData()
  formData.append('file', file)
  return http.post('/documents/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

export function listDocuments(page = 1, pageSize = 20) {
  return http.get('/documents/', { params: { page, page_size: pageSize } })
}

export function deleteDocument(documentId) {
  return http.delete(`/documents/${documentId}`)
}

export function parseDocument(documentId) {
  return http.post(`/documents/${documentId}/parse`)
}

// ============================================================
// 管理后台 API
// ============================================================

// 用户管理
export function listUsers(page = 1, pageSize = 20) {
  return http.get('/admin/users', { params: { page, page_size: pageSize } })
}

export function createUser(userData) {
  return http.post('/admin/users', userData)
}

export function getUser(userId) {
  return http.get(`/admin/users/${userId}`)
}

export function updateUser(userId, userData) {
  return http.put(`/admin/users/${userId}`, userData)
}

export function deleteUser(userId) {
  return http.delete(`/admin/users/${userId}`)
}

// 角色管理
export function listRoles() {
  return http.get('/admin/roles')
}

export function createRole(roleData) {
  return http.post('/admin/roles', roleData)
}

export function getRole(roleId) {
  return http.get(`/admin/roles/${roleId}`)
}

export function updateRole(roleId, roleData) {
  return http.put(`/admin/roles/${roleId}`, roleData)
}

export function deleteRole(roleId) {
  return http.delete(`/admin/roles/${roleId}`)
}

// 审计日志
export function listAuditLogs(page = 1, pageSize = 50, filters = {}) {
  const params = { page, page_size: pageSize, ...filters }
  return http.get('/admin/audit-logs', { params })
}

export function getAuditLog(logId) {
  return http.get(`/admin/audit-logs/${logId}`)
}

export default http

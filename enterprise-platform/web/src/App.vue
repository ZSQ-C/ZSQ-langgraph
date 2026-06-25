<template>
  <el-container class="app-container">
    <!-- 顶部导航 -->
    <el-header class="app-header">
      <div class="header-left">
        <h2>企业智能数据分析平台</h2>
      </div>
      <div class="header-right">
        <span class="user-info" v-if="userInfo">
          <el-icon><User /></el-icon>
          {{ userInfo.username }} ({{ userInfo.dept }})
        </span>
        <el-button type="danger" size="small" @click="handleLogout" v-if="isLoggedIn">
          退出登录
        </el-button>
      </div>
    </el-header>

    <!-- 未登录状态：登录表单 -->
    <el-main v-if="!isLoggedIn" class="login-container">
      <el-card class="login-card">
        <template #header><h3>用户登录</h3></template>
        <el-form :model="loginForm" label-width="80px" @submit.prevent="handleLogin">
          <el-form-item label="用户名">
            <el-input v-model="loginForm.username" placeholder="请输入用户名" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="loginForm.password" type="password" placeholder="请输入密码" show-password />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="handleLogin" :loading="loginLoading">登录</el-button>
          </el-form-item>
        </el-form>
      </el-card>
    </el-main>

    <!-- 已登录状态：主界面 -->
    <el-main v-else class="main-content">
      <el-tabs v-model="activeTab" type="border-card" class="main-tabs">
        <!-- ==================== 智能对话 Tab ==================== -->
        <el-tab-pane label="智能对话" name="chat">
          <el-container class="chat-layout">
            <!-- 左侧：会话列表 -->
            <el-aside width="260px" class="chat-sidebar">
              <div class="sidebar-header">
                <el-button type="primary" @click="handleCreateSession" :icon="'Plus'" size="small">
                  新建对话
                </el-button>
              </div>
              <el-menu class="session-list" :default-active="currentSessionId" @select="handleSelectSession">
                <el-menu-item
                  v-for="s in sessions"
                  :key="s.id"
                  :index="s.id"
                  class="session-item"
                >
                  <template #default>
                    <span class="session-title">{{ s.title }}</span>
                    <el-button
                      type="danger"
                      size="small"
                      :icon="'Delete'"
                      circle
                      @click.stop="handleDeleteSession(s.id)"
                      class="session-delete-btn"
                    />
                  </template>
                  <template #title>
                    <span class="session-title-text">{{ s.title }}</span>
                  </template>
                </el-menu-item>
              </el-menu>
              <div v-if="sessions.length === 0" class="empty-sessions">
                暂无会话，请新建
              </div>
            </el-aside>

            <!-- 右侧：对话区 -->
            <el-container class="chat-main">
              <!-- 消息列表 -->
              <el-main class="message-area" ref="messageArea">
                <div v-if="!currentSessionId" class="empty-chat">
                  请选择或创建一个会话开始对话
                </div>
                <div v-else class="message-list" ref="messageList">
                  <div
                    v-for="(msg, idx) in currentMessages"
                    :key="idx"
                    :class="['message-item', msg.role === 'user' ? 'message-user' : 'message-assistant']"
                  >
                    <div class="message-avatar">
                      <el-icon v-if="msg.role === 'user'"><User /></el-icon>
                      <el-icon v-else><Cpu /></el-icon>
                    </div>
                    <div class="message-bubble">
                      <div class="message-content" v-html="msg.content"></div>
                      <div class="message-meta">
                        <span class="message-event" v-if="msg.event">{{ msg.event }}</span>
                      </div>
                    </div>
                  </div>
                  <!-- 流式输出中的内容 -->
                  <div v-if="streamingContent" class="message-item message-assistant">
                    <div class="message-avatar"><el-icon><Cpu /></el-icon></div>
                    <div class="message-bubble streaming">
                      <div class="message-content" v-html="streamingContent"></div>
                      <span class="streaming-indicator">▌</span>
                    </div>
                  </div>
                </div>
              </el-main>

              <!-- 输入框 -->
              <el-footer class="input-area" v-if="currentSessionId">
                <el-input
                  v-model="inputMessage"
                  type="textarea"
                  :rows="3"
                  placeholder="输入您的问题，例如：查询华东区本月销售总额"
                  @keydown.enter.exact.prevent="handleSendMessage"
                />
                <div class="input-actions">
                  <el-button
                    type="primary"
                    @click="handleSendMessage"
                    :loading="isStreaming"
                    :disabled="!inputMessage.trim()"
                  >
                    <el-icon><Promotion /></el-icon>
                    {{ isStreaming ? '处理中...' : '发送' }}
                  </el-button>
                  <el-button
                    v-if="isStreaming"
                    type="warning"
                    @click="handleStopStreaming"
                  >
                    停止
                  </el-button>
                </div>
              </el-footer>
            </el-container>
          </el-container>
        </el-tab-pane>

        <!-- ==================== 管理后台 Tab ==================== -->
        <el-tab-pane label="管理后台" name="admin">
          <el-tabs v-model="adminTab" type="card" class="admin-tabs">
            <!-- 用户管理 -->
            <el-tab-pane label="用户管理" name="users">
              <div class="admin-toolbar">
                <el-button type="primary" @click="showUserDialog = true">新增用户</el-button>
              </div>
              <el-table :data="adminUsers" border stripe v-loading="adminLoading" style="width: 100%">
                <el-table-column prop="username" label="用户名" width="120" />
                <el-table-column prop="dept" label="部门" width="120" />
                <el-table-column prop="role_name" label="角色" width="100">
                  <template #default="{ row }">
                    <el-tag size="small">{{ row.role_name || '-' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="is_active" label="状态" width="80">
                  <template #default="{ row }">
                    <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">
                      {{ row.is_active ? '启用' : '禁用' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="create_time" label="创建时间" width="180" />
                <el-table-column label="操作" width="200">
                  <template #default="{ row }">
                    <el-button size="small" @click="handleEditUser(row)">编辑</el-button>
                    <el-button size="small" type="danger" @click="handleDeleteUser(row.id)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-tab-pane>

            <!-- 文档管理 -->
            <el-tab-pane label="文档管理" name="documents">
              <div class="admin-toolbar">
                <el-upload
                  :before-upload="handleBeforeUpload"
                  :show-file-list="false"
                  accept=".pdf,.docx,.doc,.md,.txt,.csv,.xlsx,.log,.json,.png,.jpg,.jpeg"
                >
                  <el-button type="primary">上传文档</el-button>
                </el-upload>
              </div>
              <el-table :data="adminDocuments" border stripe v-loading="adminDocLoading" style="width: 100%">
                <el-table-column prop="title" label="标题" min-width="200" />
                <el-table-column prop="file_type" label="类型" width="80">
                  <template #default="{ row }">
                    <el-tag size="small">{{ row.file_type || '-' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="is_parsed" label="解析状态" width="100">
                  <template #default="{ row }">
                    <el-tag :type="row.is_parsed ? 'success' : 'warning'" size="small">
                      {{ row.is_parsed ? '已解析' : '未解析' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="chunk_count" label="切片数" width="80" />
                <el-table-column prop="create_time" label="上传时间" width="180" />
                <el-table-column label="操作" width="200">
                  <template #default="{ row }">
                    <el-button size="small" @click="handleParseDocument(row.id)" :disabled="row.is_parsed">
                      解析
                    </el-button>
                    <el-button size="small" type="danger" @click="handleDeleteDocument(row.id)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-tab-pane>

            <!-- 审计日志 -->
            <el-tab-pane label="审计日志" name="audit">
              <el-form :model="auditFilters" inline class="audit-filters">
                <el-form-item label="风险等级">
                  <el-select v-model="auditFilters.risk_level" clearable placeholder="全部">
                    <el-option label="低" value="low" />
                    <el-option label="中" value="medium" />
                    <el-option label="高" value="high" />
                  </el-select>
                </el-form-item>
                <el-form-item>
                  <el-button type="primary" @click="fetchAuditLogs">查询</el-button>
                </el-form-item>
              </el-form>
              <el-table :data="adminAuditLogs" border stripe v-loading="adminAuditLoading" style="width: 100%">
                <el-table-column prop="original_query" label="查询内容" min-width="200" show-overflow-tooltip />
                <el-table-column prop="risk_level" label="风险等级" width="100">
                  <template #default="{ row }">
                    <el-tag
                      :type="row.risk_level === 'high' ? 'danger' : row.risk_level === 'medium' ? 'warning' : 'success'"
                      size="small"
                    >
                      {{ row.risk_level || '-' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="execution_success" label="执行结果" width="100">
                  <template #default="{ row }">
                    <el-tag :type="row.execution_success ? 'success' : 'danger'" size="small">
                      {{ row.execution_success !== null ? (row.execution_success ? '成功' : '失败') : '-' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="row_count" label="返回行数" width="80" />
                <el-table-column prop="execution_time_ms" label="耗时(ms)" width="100" />
                <el-table-column prop="create_time" label="时间" width="180" />
                <el-table-column label="操作" width="100">
                  <template #default="{ row }">
                    <el-button size="small" @click="showAuditDetail(row)">详情</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-tab-pane>
          </el-tabs>
        </el-tab-pane>
      </el-tabs>
    </el-main>

    <!-- 用户编辑对话框 -->
    <el-dialog v-model="showUserDialog" :title="editingUser ? '编辑用户' : '新增用户'" width="500px">
      <el-form :model="userForm" label-width="80px">
        <el-form-item label="用户名">
          <el-input v-model="userForm.username" />
        </el-form-item>
        <el-form-item label="密码" v-if="!editingUser">
          <el-input v-model="userForm.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码" v-if="editingUser">
          <el-input v-model="userForm.password" type="password" show-password placeholder="留空不修改" />
        </el-form-item>
        <el-form-item label="部门">
          <el-input v-model="userForm.dept" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="userForm.role_id" placeholder="选择角色">
            <el-option
              v-for="r in adminRoles"
              :key="r.id"
              :label="r.role_name"
              :value="r.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="启用" v-if="editingUser">
          <el-switch v-model="userForm.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showUserDialog = false">取消</el-button>
        <el-button type="primary" @click="handleSaveUser" :loading="adminLoading">
          {{ editingUser ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 审计日志详情对话框 -->
    <el-dialog v-model="showAuditDialog" title="审计日志详情" width="700px">
      <el-descriptions :column="2" border v-if="auditDetail">
        <el-descriptions-item label="查询内容" :span="2">{{ auditDetail.original_query }}</el-descriptions-item>
        <el-descriptions-item label="复杂度">{{ auditDetail.query_complexity || '-' }}</el-descriptions-item>
        <el-descriptions-item label="风险等级">
          <el-tag
            :type="auditDetail.risk_level === 'high' ? 'danger' : auditDetail.risk_level === 'medium' ? 'warning' : 'success'"
            size="small"
          >{{ auditDetail.risk_level || '-' }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="生成SQL" :span="2">
          <pre class="sql-pre">{{ auditDetail.generated_sql || '-' }}</pre>
        </el-descriptions-item>
        <el-descriptions-item label="执行SQL" :span="2">
          <pre class="sql-pre">{{ auditDetail.executed_sql || '-' }}</pre>
        </el-descriptions-item>
        <el-descriptions-item label="SQL安全">
          <el-tag :type="auditDetail.sql_safe ? 'success' : 'danger'" size="small">{{ auditDetail.sql_safe ? '通过' : '未通过' }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="权限检查">
          <el-tag :type="auditDetail.permission_pass ? 'success' : 'danger'" size="small">{{ auditDetail.permission_pass ? '通过' : '未通过' }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="执行结果">
          <el-tag :type="auditDetail.execution_success ? 'success' : 'danger'" size="small">{{ auditDetail.execution_success ? '成功' : '失败' }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="返回行数">{{ auditDetail.row_count ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="执行耗时">{{ auditDetail.execution_time_ms ? auditDetail.execution_time_ms + 'ms' : '-' }}</el-descriptions-item>
        <el-descriptions-item label="错误信息" :span="2">{{ auditDetail.error_message || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </el-container>
</template>

<script setup>
import { ref, reactive, computed, onMounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  login as apiLogin,
  getCurrentUser as apiGetCurrentUser,
  createSession,
  listSessions,
  deleteSession,
  sendMessage,
  listUsers,
  createUser,
  updateUser,
  deleteUser,
  listRoles,
  listDocuments,
  deleteDocument,
  parseDocument,
  listAuditLogs,
  uploadDocument,
} from './api.js'

// ============================================================
// 状态管理
// ============================================================

const isLoggedIn = ref(false)
const userInfo = ref(null)
const activeTab = ref('chat')
const adminTab = ref('users')

// 登录表单
const loginForm = reactive({ username: '', password: '' })
const loginLoading = ref(false)

// 对话状态
const sessions = ref([])
const currentSessionId = ref(null)
const currentMessages = ref([])
const inputMessage = ref('')
const streamingContent = ref('')
const isStreaming = ref(false)
let streamController = null

// 管理后台状态
const adminUsers = ref([])
const adminRoles = ref([])
const adminDocuments = ref([])
const adminAuditLogs = ref([])
const adminLoading = ref(false)
const adminDocLoading = ref(false)
const adminAuditLoading = ref(false)

// 用户对话框
const showUserDialog = ref(false)
const editingUser = ref(null)
const userForm = reactive({ username: '', password: '', dept: '', role_id: '', is_active: true })

// 审计详情
const showAuditDialog = ref(false)
const auditDetail = ref(null)
const auditFilters = reactive({ risk_level: null })

// 消息列表引用
const messageList = ref(null)

// ============================================================
// 生命周期
// ============================================================

onMounted(async () => {
  const token = localStorage.getItem('access_token')
  if (token) {
    try {
      const res = await apiGetCurrentUser()
      userInfo.value = res.data
      isLoggedIn.value = true
      await loadSessions()
    } catch (e) {
      localStorage.removeItem('access_token')
      localStorage.removeItem('refresh_token')
    }
  }
})

// ============================================================
// 登录 / 退出
// ============================================================

async function handleLogin() {
  if (!loginForm.username || !loginForm.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loginLoading.value = true
  try {
    const res = await apiLogin(loginForm.username, loginForm.password)
    const { access_token } = res.data
    localStorage.setItem('access_token', access_token)
    isLoggedIn.value = true
    const userRes = await apiGetCurrentUser()
    userInfo.value = userRes.data
    await loadSessions()
    ElMessage.success('登录成功')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  } finally {
    loginLoading.value = false
  }
}

function handleLogout() {
  localStorage.removeItem('access_token')
  localStorage.removeItem('refresh_token')
  isLoggedIn.value = false
  userInfo.value = null
  sessions.value = []
  currentSessionId.value = null
  currentMessages.value = []
  activeTab.value = 'chat'
  ElMessage.info('已退出登录')
}

// ============================================================
// 会话管理
// ============================================================

async function loadSessions() {
  try {
    const res = await listSessions()
    sessions.value = res.data.items
  } catch (e) {
    console.error('加载会话列表失败:', e)
  }
}

async function handleCreateSession() {
  try {
    const res = await createSession()
    const newSession = res.data
    sessions.value.unshift(newSession)
    currentSessionId.value = newSession.id
    currentMessages.value = []
    ElMessage.success('新建会话成功')
  } catch (e) {
    ElMessage.error('创建会话失败')
  }
}

function handleSelectSession(sessionId) {
  currentSessionId.value = sessionId
  currentMessages.value = []
  streamingContent.value = ''
}

async function handleDeleteSession(sessionId) {
  try {
    await ElMessageBox.confirm('确定删除此会话？', '确认删除', { type: 'warning' })
    await deleteSession(sessionId)
    sessions.value = sessions.value.filter((s) => s.id !== sessionId)
    if (currentSessionId.value === sessionId) {
      currentSessionId.value = null
      currentMessages.value = []
    }
    ElMessage.success('会话已删除')
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

// ============================================================
// 消息发送 / SSE 流式接收
// ============================================================

async function handleSendMessage() {
  if (!inputMessage.value.trim() || isStreaming.value) return

  const message = inputMessage.value.trim()
  const sessionId = currentSessionId.value
  if (!sessionId) return

  // 添加用户消息
  currentMessages.value.push({ role: 'user', content: message })
  inputMessage.value = ''
  streamingContent.value = ''
  isStreaming.value = true

  // 添加助手消息占位
  const assistantMsgIdx = currentMessages.value.length
  currentMessages.value.push({ role: 'assistant', content: '', event: '' })

  streamController = sendMessage(sessionId, message, {
    onEvent(eventType, data) {
      if (eventType === 'stream' || eventType === 'chunk') {
        if (data.content) {
          currentMessages.value[assistantMsgIdx].content += data.content
          streamingContent.value = ''
        }
      } else if (eventType === 'node_start') {
        currentMessages.value[assistantMsgIdx].event = `正在执行: ${data.node}`
      } else if (eventType === 'node_end') {
        currentMessages.value[assistantMsgIdx].event = `${data.node} 完成`
      } else if (eventType === 'error') {
        currentMessages.value[assistantMsgIdx].content += `\n\n错误: ${data.message}`
        ElMessage.error(data.message || '发生错误')
      } else if (eventType === 'interrupt') {
        currentMessages.value[assistantMsgIdx].content += `\n\n[${data.message}]`
        ElMessage.warning(data.message || '需要人工审批')
      }
    },
    onError(err) {
      currentMessages.value[assistantMsgIdx].content += `\n\n[错误: ${err.message}]`
      ElMessage.error('连接出错: ' + err.message)
      isStreaming.value = false
    },
    onComplete() {
      isStreaming.value = false
      streamingContent.value = ''
      // 刷新会话列表以更新 last_message
      loadSessions()
    },
  })

  await nextTick()
  scrollToBottom()
}

function handleStopStreaming() {
  if (streamController) {
    streamController.abort()
    streamController = null
  }
  isStreaming.value = false
  streamingContent.value = ''
  ElMessage.info('已停止')
}

function scrollToBottom() {
  nextTick(() => {
    if (messageList.value) {
      messageList.value.scrollTop = messageList.value.scrollHeight
    }
  })
}

// ============================================================
// 管理后台 - 用户
// ============================================================

async function fetchUsers() {
  adminLoading.value = true
  try {
    const res = await listUsers()
    adminUsers.value = res.data
  } catch (e) {
    ElMessage.error('加载用户列表失败')
  } finally {
    adminLoading.value = false
  }
}

function handleEditUser(user) {
  editingUser.value = user
  userForm.username = user.username
  userForm.password = ''
  userForm.dept = user.dept
  userForm.role_id = user.role_id
  userForm.is_active = user.is_active
  showUserDialog.value = true
}

async function handleDeleteUser(userId) {
  try {
    await ElMessageBox.confirm('确定删除此用户？', '确认删除', { type: 'warning' })
    await deleteUser(userId)
    ElMessage.success('用户已删除')
    await fetchUsers()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

async function handleSaveUser() {
  if (!userForm.username || (!editingUser.value && !userForm.password) || !userForm.dept || !userForm.role_id) {
    ElMessage.warning('请填写完整信息')
    return
  }
  adminLoading.value = true
  try {
    if (editingUser.value) {
      const updateData = {
        username: userForm.username,
        dept: userForm.dept,
        role_id: userForm.role_id,
        is_active: userForm.is_active,
      }
      if (userForm.password) updateData.password = userForm.password
      await updateUser(editingUser.value.id, updateData)
      ElMessage.success('用户已更新')
    } else {
      await createUser({
        username: userForm.username,
        password: userForm.password,
        dept: userForm.dept,
        role_id: userForm.role_id,
      })
      ElMessage.success('用户已创建')
    }
    showUserDialog.value = false
    editingUser.value = null
    userForm.username = ''
    userForm.password = ''
    userForm.dept = ''
    userForm.role_id = ''
    userForm.is_active = true
    await fetchUsers()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  } finally {
    adminLoading.value = false
  }
}

// ============================================================
// 管理后台 - 角色、文档、审计日志
// ============================================================

async function fetchRoles() {
  try {
    const res = await listRoles()
    adminRoles.value = res.data
  } catch (e) {
    console.error('加载角色列表失败:', e)
  }
}

async function fetchDocuments() {
  adminDocLoading.value = true
  try {
    const res = await listDocuments()
    adminDocuments.value = res.data.items
  } catch (e) {
    ElMessage.error('加载文档列表失败')
  } finally {
    adminDocLoading.value = false
  }
}

async function fetchAuditLogs() {
  adminAuditLoading.value = true
  try {
    const filters = {}
    if (auditFilters.risk_level) filters.risk_level = auditFilters.risk_level
    const res = await listAuditLogs(1, 50, filters)
    adminAuditLogs.value = res.data.items
  } catch (e) {
    ElMessage.error('加载审计日志失败')
  } finally {
    adminAuditLoading.value = false
  }
}

async function handleBeforeUpload(file) {
  try {
    await uploadDocument(file)
    ElMessage.success(`文档 "${file.name}" 上传成功`)
    await fetchDocuments()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '上传失败')
  }
  return false // 阻止 Element Plus 默认上传行为
}

async function handleDeleteDocument(docId) {
  try {
    await ElMessageBox.confirm('确定删除此文档？', '确认删除', { type: 'warning' })
    await deleteDocument(docId)
    ElMessage.success('文档已删除')
    await fetchDocuments()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

async function handleParseDocument(docId) {
  try {
    const res = await parseDocument(docId)
    ElMessage.success(res.data.message || '解析任务已提交')
    await fetchDocuments()
  } catch (e) {
    ElMessage.error('触发解析失败')
  }
}

function showAuditDetail(row) {
  auditDetail.value = row
  showAuditDialog.value = true
}

// ============================================================
// Tab 切换时加载数据
// ============================================================

// 使用简单的 watch 模式，当切换到管理后台时自动加载数据
import { watch } from 'vue'
watch(activeTab, async (newTab) => {
  if (newTab === 'admin') {
    await fetchUsers()
    await fetchRoles()
    await fetchDocuments()
  }
})

watch(adminTab, async (newTab) => {
  if (newTab === 'audit') {
    await fetchAuditLogs()
  } else if (newTab === 'users') {
    await fetchUsers()
  } else if (newTab === 'documents') {
    await fetchDocuments()
  }
})
</script>

<style>
/* ============================================================
   全局样式
   ============================================================ */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', Arial, sans-serif;
  background-color: #f0f2f5;
}

.app-container {
  height: 100vh;
  display: flex;
  flex-direction: column;
}

/* 顶部导航 */
.app-header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 60px;
  flex-shrink: 0;
}

.app-header h2 {
  font-size: 18px;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 14px;
}

/* 登录页面 */
.login-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 60px);
}

.login-card {
  width: 400px;
}

.login-card h3 {
  text-align: center;
}

/* 主内容区 */
.main-content {
  flex: 1;
  padding: 12px;
  overflow: hidden;
}

.main-tabs {
  height: calc(100vh - 84px);
}

.main-tabs .el-tab-pane {
  height: calc(100vh - 140px);
  overflow: hidden;
}

/* 对话布局 */
.chat-layout {
  height: 100%;
}

.chat-sidebar {
  border-right: 1px solid #e8e8e8;
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  padding: 12px;
  border-bottom: 1px solid #e8e8e8;
}

.sidebar-header .el-button {
  width: 100%;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  border-right: none;
}

.session-item {
  position: relative;
  font-size: 13px;
}

.session-title-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 170px;
  display: inline-block;
}

.session-delete-btn {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  opacity: 0;
  transition: opacity 0.2s;
  width: 24px;
  height: 24px;
  min-height: 24px;
}

.session-item:hover .session-delete-btn {
  opacity: 1;
}

.empty-sessions {
  text-align: center;
  padding: 40px 0;
  color: #999;
  font-size: 14px;
}

/* 聊天主区域 */
.chat-main {
  display: flex;
  flex-direction: column;
}

.message-area {
  flex: 1;
  overflow-y: auto;
  padding: 16px 24px;
  background-color: #fafafa;
}

.empty-chat {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #999;
  font-size: 16px;
}

.message-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.message-item {
  display: flex;
  gap: 10px;
  max-width: 85%;
}

.message-user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message-assistant {
  align-self: flex-start;
}

.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background-color: #e0e0e0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.message-user .message-avatar {
  background-color: #667eea;
  color: white;
}

.message-bubble {
  padding: 10px 14px;
  border-radius: 12px;
  background-color: #ffffff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  position: relative;
}

.message-user .message-bubble {
  background-color: #667eea;
  color: white;
}

.message-content {
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-meta {
  margin-top: 6px;
  font-size: 12px;
}

.message-event {
  color: #999;
  font-style: italic;
}

.message-user .message-event {
  color: rgba(255, 255, 255, 0.8);
}

.streaming .message-content {
  display: inline;
}

.streaming-indicator {
  animation: blink 1s infinite;
  color: #667eea;
  font-weight: bold;
}

@keyframes blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}

/* 输入区域 */
.input-area {
  padding: 12px 24px;
  background-color: #ffffff;
  border-top: 1px solid #e8e8e8;
}

.input-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

/* 管理后台 */
.admin-tabs {
  height: 100%;
}

.admin-tabs .el-tab-pane {
  height: calc(100% - 42px);
  overflow-y: auto;
}

.admin-toolbar {
  margin-bottom: 12px;
}

.audit-filters {
  margin-bottom: 12px;
}

.sql-pre {
  background: #f5f5f5;
  padding: 8px;
  border-radius: 4px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
  margin: 0;
}
</style>

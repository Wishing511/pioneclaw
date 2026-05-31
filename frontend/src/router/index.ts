import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useUserStore } from '@/stores/user'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { requiresAuth: false }
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    meta: { requiresAuth: true },
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/Dashboard.vue'),
        meta: { title: 'Dashboard', icon: 'Odometer', group: 'core' }
      },
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/views/Chat.vue'),
        meta: { title: 'Chat', icon: 'ChatDotRound', group: 'core' }
      },
      {
        path: 'agents',
        name: 'Agents',
        component: () => import('@/views/Agents.vue'),
        meta: { title: 'Agents', icon: 'Cpu', group: 'core' }
      },
      {
        path: 'skills',
        name: 'Skills',
        component: () => import('@/views/Skills.vue'),
        meta: { title: 'Skills', icon: 'Tools', group: 'core' }
      },
      {
        path: 'skill-eval',
        name: 'SkillEval',
        component: () => import('@/views/SkillEval.vue'),
        meta: { title: 'SkillEval', icon: 'TrendCharts', group: 'core' }
      },
      {
        path: 'tasks',
        name: 'Tasks',
        component: () => import('@/views/Tasks.vue'),
        meta: { title: 'Tasks', icon: 'List', group: 'core' }
      },
      {
        path: 'cron',
        name: 'Cron',
        component: () => import('@/views/Cron.vue'),
        meta: { title: 'Cron Jobs', icon: 'Timer', group: 'core' }
      },
      {
        path: 'tracing',
        name: 'Tracing',
        component: () => import('@/views/Tracing.vue'),
        meta: { title: 'Tracing', icon: 'Connection', group: 'core' }
      },
      {
        path: 'layered-memory',
        name: 'Memory',
        component: () => import('@/views/MemoryView.vue'),
        meta: { title: 'Memory', icon: 'Memo', group: 'memory' }
      },
      {
        path: 'autodream',
        name: 'AutoDream',
        component: () => import('@/views/AutoDream.vue'),
        meta: { title: 'AutoDream', icon: 'MagicStick', group: 'memory' }
      },
      {
        path: 'wiki',
        name: 'Wiki',
        component: () => import('@/views/Wiki.vue'),
        meta: { title: 'Wiki', icon: 'Notebook', group: 'knowledge' }
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('@/views/Profile.vue'),
        meta: { title: 'Profile', icon: 'User', group: 'user' }
      },
      // 系统管理分组
      {
        path: 'user-management',
        name: 'UserManagement',
        component: () => import('@/views/UserManagement.vue'),
        meta: { title: 'User & Permissions', icon: 'User', group: 'admin', roles: ['super_admin', 'org_admin'] }
      },
      {
        path: 'ai-management',
        name: 'AIManagement',
        component: () => import('@/views/AIManagement.vue'),
        meta: { title: 'AI Management', icon: 'MagicStick', group: 'admin' }
      },
      {
        path: 'extension-management',
        name: 'ExtensionManagement',
        component: () => import('@/views/ExtensionManagement.vue'),
        meta: { title: 'Extension Management', icon: 'Connection', group: 'admin', roles: ['super_admin', 'org_admin'] }
      },
      {
        path: 'system-ops',
        name: 'SystemOps',
        component: () => import('@/views/SystemOps.vue'),
        meta: { title: 'System Operations', icon: 'Setting', group: 'admin', roles: ['super_admin'] }
      },
      {
        path: 'security-gateway',
        name: 'SecurityGateway',
        component: () => import('@/views/SecurityGateway.vue'),
        meta: { title: 'Security Gateway', icon: 'Lock', group: 'admin', roles: ['super_admin', 'org_admin'] }
      }
    ]
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫
router.beforeEach(async (to, _from, next) => {
  const userStore = useUserStore()

  // 等待 session 恢复完成（首次加载时调用 /auth/refresh-token）
  if (!userStore.isInitialized) {
    await userStore.restoreSession()
  }

  if (to.meta.requiresAuth !== false && !userStore.token) {
    next('/login')
  } else if (to.path === '/login' && userStore.token) {
    next('/dashboard')
  } else if (to.meta.roles) {
    const allowedRoles = to.meta.roles as string[]
    if (!allowedRoles.includes(userStore.user?.role || '')) {
      next('/dashboard')
    } else {
      next()
    }
  } else {
    next()
  }
})

export default router
<template>
  <div class="main-layout" :class="{ 'sidebar-collapsed': isCollapsed }">
    <!-- 侧边栏 -->
    <aside class="sidebar" :style="{ width: isCollapsed ? '64px' : '220px' }">
      <div class="sidebar-logo">
        <div class="logo-icon">
          <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M16 2L28 9V23L16 30L4 23V9L16 2Z" stroke="currentColor" stroke-width="1.5" fill="none"/>
            <circle cx="16" cy="16" r="4" fill="currentColor" opacity="0.6"/>
            <path d="M16 6V12M16 20V26M8 12L12 14M20 18L24 20M8 20L12 18M20 14L24 12" stroke="currentColor" stroke-width="1" opacity="0.4"/>
          </svg>
        </div>
        <transition name="fade">
          <span v-show="!isCollapsed" class="logo-text">PioneClaw</span>
        </transition>
      </div>
      <!-- Collapse button always visible at bottom of sidebar -->
      <button class="sidebar-collapse-btn" @click="isCollapsed = !isCollapsed">
        <svg v-if="!isCollapsed" width="14" height="14" viewBox="0 0 16 16" fill="none">
          <path d="M10 3L5 8L10 13" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="none">
          <path d="M6 3L11 8L6 13" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>

      <nav class="sidebar-nav">
        <template v-for="group in navGroups" :key="group.key">
          <div class="nav-group">
            <div class="nav-group-title" v-show="!isCollapsed">{{ $t(`navGroups.${group.key}`) }}</div>
            <router-link
              v-for="item in group.items"
              :key="item.path"
              :to="item.path"
              class="nav-item"
              :class="{ active: activeMenu === item.path }"
              :title="isCollapsed ? $t(navKeyMap[item.path] || '') : ''"
            >
              <el-icon><component :is="iconMap[item.meta.icon as string]" /></el-icon>
              <span class="nav-label" v-show="!isCollapsed">{{ $t(navKeyMap[item.path] || '') }}</span>
            </router-link>
          </div>
        </template>
      </nav>

    </aside>

    <!-- 主内容区 -->
    <div class="main-container">
      <!-- 顶部导航 -->
      <header class="header">
        <div class="header-left">
          <!-- 页面标题已移到各页面内部 -->
        </div>

        <div class="header-right">
          <button class="icon-btn" @click="themeStore.toggle()">
            <el-icon :size="18"><Sunny v-if="themeStore.isDark" /><Moon v-else /></el-icon>
          </button>
          <div class="user-menu" @click="showUserMenu = !showUserMenu">
            <div class="user-avatar">
              <el-avatar :size="28" :src="userStore.avatar">
                <el-icon><User /></el-icon>
              </el-avatar>
            </div>
            <span class="user-name">{{ userStore.displayName }}</span>
            <el-icon :size="12"><ArrowDown /></el-icon>
          </div>
          <!-- 简易下拉 -->
          <transition name="dropdown">
            <div v-if="showUserMenu" class="user-dropdown">
              <div class="dropdown-item" @click="router.push('/profile'); showUserMenu = false">
                <el-icon><UserFilled /></el-icon> {{ $t('nav.profile') }}
              </div>
              <div class="dropdown-divider"></div>
              <div class="dropdown-item logout" @click="userStore.logout(); router.push('/login')">
                <el-icon><SwitchButton /></el-icon> {{ $t('nav.logout') }}
              </div>
            </div>
          </transition>
        </div>
      </header>

      <!-- 内容区 -->
      <main class="content">
        <router-view v-slot="{ Component }">
          <transition name="page">
            <component :is="Component" :key="$route.path" />
          </transition>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useThemeStore } from '@/stores/theme'
import { ElMessage } from 'element-plus'
import {
  Odometer, Cpu, Connection, Tools, Memo,
  MagicStick, Setting, ArrowDown, User, Histogram,
  ChatDotRound, Folder, List, Timer, Collection, Notebook,
  Sunny, Moon, SwitchButton, TrendCharts, Lock
} from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const themeStore = useThemeStore()

const isCollapsed = ref(false)
const showUserMenu = ref(false)

const activeMenu = computed(() => route.path)

// Icon name to component mapping
const iconMap: Record<string, Component> = {
  Odometer, Cpu, ChatDotRound, Tools, List, Timer,
  Memo, Collection, Histogram, Notebook, Folder,
  User, MagicStick, Connection, Setting, TrendCharts, Lock
}

// Nav i18n key mapping: route path -> nav key
const navKeyMap: Record<string, string> = {
  '/dashboard': 'nav.dashboard',
  '/chat': 'nav.chat',
  '/agents': 'nav.agents',
  '/skills': 'nav.skills',
  '/skill-eval': 'nav.skillEval',
  '/tasks': 'nav.tasks',
  '/cron': 'nav.cron',
  '/tracing': 'nav.tracing',
  '/layered-memory': 'nav.memory',
  '/autodream': 'nav.autoDream',
  '/wiki': 'nav.wiki',
  '/user-management': 'nav.userManagement',
  '/ai-management': 'nav.aiManagement',
  '/extension-management': 'nav.extensionManagement',
  '/system-ops': 'nav.systemOps',
  '/security-gateway': 'nav.securityGateway',
}

// Route visibility check
function isNavVisible(routeMeta: any): boolean {
  if (!routeMeta.roles) return true
  const roles = routeMeta.roles as string[]
  if (roles.includes('super_admin') && userStore.isSuperAdmin) return true
  if (roles.includes('org_admin') && userStore.isAdmin) return true
  return false
}

// Grouped nav items computed from router config
const navGroups = computed(() => {
  const groupOrder = ['core', 'memory', 'knowledge', 'admin']
  const groups: Record<string, { key: string; items: any[] }> = {}

  // Get all resolved routes and filter to children of root layout
  const allRoutes = router.getRoutes()
  const children = allRoutes.filter(r => {
    // Only direct children of the root layout (path like /dashboard, /chat, etc.)
    const parts = r.path.split('/').filter(Boolean)
    return parts.length === 1 && r.meta.group
  })

  for (const child of children) {
    const meta = child.meta as any
    const group = meta.group
    if (group === 'user') continue
    if (!isNavVisible(meta)) continue

    if (!groups[group]) {
      groups[group] = { key: group, items: [] }
    }
    groups[group].items.push({
      path: child.path,
      meta: meta,
      name: child.name,
    })
  }

  return groupOrder.filter(k => groups[k]).map(k => groups[k])
})

// 点击外部关闭下拉
function handleClickOutside(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (!target.closest('.user-menu') && !target.closest('.user-dropdown')) {
    showUserMenu.value = false
  }
}

// 键盘快捷键处理
function handleKeyboardShortcuts(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault()
    router.push('/chat')
    ElMessage.info(t('shortcuts.search'))
  }

  if ((e.metaKey || e.ctrlKey) && e.key === 'n') {
    e.preventDefault()
    router.push('/tasks')
    ElMessage.info(t('shortcuts.newTask'))
  }

  if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'N') {
    e.preventDefault()
    router.push('/agents')
    ElMessage.info(t('shortcuts.newAgent'))
  }

  if ((e.metaKey || e.ctrlKey) && e.key === 'g') {
    e.preventDefault()
    themeStore.toggle()
    ElMessage.success(themeStore.isDark ? t('shortcuts.darkMode') : t('shortcuts.lightMode'))
  }

  if ((e.metaKey || e.ctrlKey) && e.key === '/') {
    e.preventDefault()
    ElMessage.info({
      message: t('shortcuts.help'),
      duration: 5000
    })
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
  document.addEventListener('keydown', handleKeyboardShortcuts)
})
onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
  document.removeEventListener('keydown', handleKeyboardShortcuts)
})
</script>

<style scoped lang="scss">
.main-layout {
  display: flex;
  height: 100vh;
  width: 100%;
  background: var(--pc-bg-deep);
  overflow: hidden;
}

// === 侧边栏 ===
.sidebar {
  height: 100vh;
  background: var(--pc-bg-base);
  border-right: 1px solid var(--pc-border);
  display: flex;
  flex-direction: column;
  transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  flex-shrink: 0;
  position: relative;

  .sidebar-logo {
    height: 64px;
    display: flex;
    align-items: center;
    padding: 0 16px;
    border-bottom: 1px solid var(--pc-border);
    gap: 12px;
    flex-shrink: 0;

    .logo-icon {
      width: 32px;
      height: 32px;
      color: var(--pc-primary);
      flex-shrink: 0;
      filter: drop-shadow(0 0 6px rgba(var(--pc-primary-rgb), 0.4));
    }

    .logo-text {
      font-size: 18px;
      font-weight: 700;
      color: var(--pc-text-primary);
      letter-spacing: -0.5px;
      background: var(--pc-gradient-primary);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      white-space: nowrap;
    }
  }

  .sidebar-collapse-btn {
    position: absolute;
    bottom: 16px;
    left: 50%;
    transform: translateX(-50%);
    width: 32px;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--pc-border);
    background: var(--pc-bg-elevated);
    color: var(--pc-text-muted);
    border-radius: var(--pc-radius-md);
    cursor: pointer;
    transition: all 0.2s;
    z-index: 10;

    &:hover {
      color: var(--pc-primary);
      border-color: var(--pc-primary);
      background: rgba(var(--pc-primary-rgb), 0.08);
      box-shadow: 0 0 12px rgba(var(--pc-primary-rgb), 0.2);
    }
  }

  .sidebar-nav {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 8px 0;
  }

  .nav-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    margin: 1px 8px;
    border-radius: var(--pc-radius-sm);
    color: var(--pc-text-secondary);
    text-decoration: none;
    font-size: 13px;
    font-weight: 400;
    transition: all 0.2s ease;
    cursor: pointer;
    white-space: nowrap;

    .el-icon {
      flex-shrink: 0;
    }

    &:hover {
      background: rgba(var(--pc-primary-rgb), 0.06);
      color: var(--pc-text-primary);
    }

    &.active {
      background: rgba(var(--pc-primary-rgb), 0.1);
      color: var(--pc-primary);
      font-weight: 500;

      &::before {
        content: '';
        position: absolute;
        left: 0;
        top: 50%;
        transform: translateY(-50%);
        width: 3px;
        height: 20px;
        background: var(--pc-primary);
        border-radius: 0 3px 3px 0;
        box-shadow: 0 0 8px rgba(var(--pc-primary-rgb), 0.5);
      }
    }

    &.sub {
      padding-left: 24px;
      font-size: 12.5px;
    }
  }

  .nav-group {
    margin-top: 4px;

    .nav-group-title {
      padding: 8px 24px 4px;
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 1.5px;
      color: var(--pc-text-muted);
      font-weight: 600;
    }
  }
}

// === 主容器 ===
.main-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--pc-bg-deep);
}

// === 顶部导航 ===
.header {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: var(--pc-glass-bg);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--pc-border);
  flex-shrink: 0;
  z-index: 10;
  transition: background 0.3s ease;

  .header-left {
    display: flex;
    align-items: center;
    gap: 12px;

    .collapse-btn {
      width: 32px;
      height: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
      border: none;
      background: transparent;
      color: var(--pc-text-secondary);
      border-radius: var(--pc-radius-sm);
      cursor: pointer;
      transition: all 0.2s;

      &:hover {
        background: rgba(var(--pc-primary-rgb), 0.06);
        color: var(--pc-text-primary);
      }
    }

    .page-title {
      font-size: 15px;
      font-weight: 500;
      color: var(--pc-text-primary);
      letter-spacing: -0.2px;
    }
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 8px;
    position: relative;

    .icon-btn {
      width: 32px;
      height: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
      border: none;
      background: transparent;
      color: var(--pc-text-secondary);
      border-radius: var(--pc-radius-sm);
      cursor: pointer;
      transition: all 0.2s;

      &:hover {
        background: rgba(var(--pc-primary-rgb), 0.06);
        color: var(--pc-primary);
      }
    }

    .user-menu {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 4px 10px;
      border-radius: var(--pc-radius-sm);
      cursor: pointer;
      transition: all 0.2s;

      &:hover {
        background: rgba(var(--pc-primary-rgb), 0.06);
      }

      .user-name {
        font-size: 13px;
        color: var(--pc-text-primary);
      }
    }

    .user-dropdown {
      position: absolute;
      top: 44px;
      right: 0;
      min-width: 160px;
      background: var(--pc-bg-elevated);
      border: 1px solid var(--pc-border);
      border-radius: var(--pc-radius-md);
      box-shadow: var(--pc-shadow-lg);
      padding: 4px;
      z-index: 100;

      .dropdown-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 12px;
        font-size: 13px;
        color: var(--pc-text-primary);
        border-radius: var(--pc-radius-sm);
        cursor: pointer;
        transition: background 0.15s;

        &:hover {
          background: rgba(var(--pc-primary-rgb), 0.08);
        }

        &.logout {
          color: var(--pc-accent-red);
        }
      }

      .dropdown-divider {
        height: 1px;
        background: var(--pc-border);
        margin: 4px 0;
      }
    }
  }
}

// === 内容区 ===
.content {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

// === 过渡动画 ===
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

.dropdown-enter-active, .dropdown-leave-active { transition: all 0.2s ease; }
.dropdown-enter-from, .dropdown-leave-to { opacity: 0; transform: translateY(-4px); }

.page-enter-active { transition: all 0.25s ease; }
.page-leave-active { transition: all 0.15s ease; }
.page-enter-from { opacity: 0; transform: translateY(6px); }
.page-leave-to { opacity: 0; transform: translateY(-4px); }
</style>

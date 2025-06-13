// src/router/index.js

import { createRouter, createWebHistory } from 'vue-router';

// --- 1. 导入你的页面组件 (这部分保持不变) ---
import ReviewList from '../components/ReviewList.vue';
import ActionsPage from '../components/ActionsPage.vue';
import EmbySettingsPage from '../components/settings/EmbySettingsPage.vue';
import ApiDataSourceSettingsPage from '../components/settings/ApiDataSourceSettingsPage.vue';
import SchedulerSettingsPage from '../components/settings/SchedulerSettingsPage.vue';
import GeneralSettingsPage from '../components/settings/GeneralSettingsPage.vue';

// ★★★ 导入我们新创建的登录组件 ★★★
import Login from '../components/Login.vue'; 

// --- 2. 定义路由规则 (修改版) ---
const routes = [
  // ★★★ 添加登录页面的路由 ★★★
  {
    path: '/login',
    name: 'Login',
    component: Login,
    meta: {
      // 这个页面不需要认证
      requiresAuth: false,
    },
  },

  // ★★★ 为你现有的所有路由添加 meta.requiresAuth ★★★
  {
    // 默认根路径重定向到全量处理页面，路由守卫会在此之前进行拦截
    path: '/',
    redirect: '/actions' 
  },
  {
    path: '/review',
    name: 'ReviewList',
    component: ReviewList,
    meta: { requiresAuth: true }, // 需要登录
  },
  {
    path: '/actions',
    name: 'actions-status',
    component: ActionsPage,
    meta: { requiresAuth: true }, // 需要登录
  },
  {
    path: '/settings/emby',
    name: 'settings-emby',
    component: EmbySettingsPage,
    meta: { requiresAuth: true }, // 需要登录
  },
  {
    path: '/settings/api',
    name: 'settings-api',
    component: ApiDataSourceSettingsPage,
    meta: { requiresAuth: true }, // 需要登录
  },
  {
    path: '/settings/scheduler',
    name: 'settings-scheduler',
    component: SchedulerSettingsPage,
    meta: { requiresAuth: true }, // 需要登录
  },
  {
    path: '/settings/general',
    name: 'settings-general',
    component: GeneralSettingsPage,
    meta: { requiresAuth: true }, // 需要登录
  },
  {
    path: '/edit-media/:itemId',
    name: 'MediaEditPage',
    component: () => import('../components/MediaEditPage.vue'),
    props: true,
    meta: { requiresAuth: true }, // 需要登录
  },
];

// --- 3. 创建路由实例 (这部分保持不变) ---
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) {
      return savedPosition;
    } else {
      return { top: 0 };
    }
  }
});

// ★★★ 4. 创建全局路由守卫 (这是新增的核心部分) ★★★
import { useAuthStore } from '../stores/auth';

router.beforeEach(async (to, from, next) => {
  // 在守卫函数内部获取 authStore 实例
  const authStore = useAuthStore();

  // 确保在进行任何判断前，我们已经从后端获取了最新的认证状态
  // 这个检查只在 store 状态未初始化时执行一次，避免重复请求
  if (authStore.username === null && authStore.initializationError === null) {
    try {
      await authStore.checkAuthStatus();
    } catch (error) {
      // 即使检查失败（比如后端服务没开），也要继续执行下去
      // 后续的逻辑会根据 initializationError 状态来处理
    }
  }
  
  const requiresAuth = to.meta.requiresAuth;
  const isAuthEnabled = authStore.isAuthEnabled;
  const isLoggedIn = authStore.isLoggedIn;

  if (requiresAuth && isAuthEnabled && !isLoggedIn) {
    // 条件：页面需要认证 && 认证功能已开启 && 用户未登录
    // 结果：重定向到登录页面
    next({ name: 'Login' });
  } else {
    // 其他所有情况都放行:
    // 1. 目标页面是登录页 (requiresAuth: false)
    // 2. 认证功能未开启 (isAuthEnabled: false)
    // 3. 用户已登录 (isLoggedIn: true)
    next();
  }
});


// --- 5. 导出路由实例 (这部分保持不变) ---
export default router;
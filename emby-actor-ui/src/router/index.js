// src/router/index.js

import { createRouter, createWebHistory } from 'vue-router';

// --- 1. 导入你的页面组件 (这部分保持不变) ---
import ReviewList from '../components/ReviewList.vue';
import ActionsPage from '../components/ActionsPage.vue';
import EmbySettingsPage from '../components/settings/EmbySettingsPage.vue';
import SchedulerSettingsPage from '../components/settings/SchedulerSettingsPage.vue';
import GeneralSettingsPage from '../components/settings/GeneralSettingsPage.vue';
import WatchlistPage from '../components/WatchlistPage.vue';
import CollectionsPage from '../components/CollectionsPage.vue';
import ActorSubscriptionPage from '../components/ActorSubscriptionPage.vue';
import Login from '../components/Login.vue'; 

// --- 2. 定义路由规则 (修改版) ---
const routes = [
  {
    path: '/login',
    name: 'Login',
    component: Login,
    meta: {
      requiresAuth: false,
    },
  },
  {
    path: '/',
    redirect: '/actions' 
  },
  {
    path: '/review',
    name: 'ReviewList',
    component: ReviewList,
    meta: { requiresAuth: true },
  },
  {
    path: '/actions',
    name: 'actions-status',
    component: ActionsPage,
    meta: { requiresAuth: true },
  },
  {
    path: '/settings/emby',
    name: 'settings-emby',
    component: EmbySettingsPage,
    meta: { requiresAuth: true },
  },
  {
    path: '/settings/scheduler',
    name: 'settings-scheduler',
    component: SchedulerSettingsPage,
    meta: { requiresAuth: true },
  },
  {
    path: '/settings/general',
    name: 'settings-general',
    component: GeneralSettingsPage,
    meta: { requiresAuth: true },
  },
  {
    path: '/watchlist',
    name: 'Watchlist',
    component: WatchlistPage,
    meta: { requiresAuth: true },
  },
  {
    path: '/collections',
    name: 'Collections',
    component: CollectionsPage,
    meta: { requiresAuth: true },
  },
  {
    path: '/edit-media/:itemId',
    name: 'MediaEditPage', // ✨✨✨ [修改] 将名字改为 MediaEditSA，以明确区分
    component: () => import('../components/MediaEditPage.vue'),
    props: true,
    meta: { requiresAuth: true },
  },
  { 
    path: '/actor-subscriptions',
    name: 'ActorSubscriptions',
    component: ActorSubscriptionPage,
    meta: { requiresAuth: true },
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

// --- 4. 创建全局路由守卫 (这部分保持不变) ---
import { useAuthStore } from '../stores/auth';

router.beforeEach(async (to, from, next) => {
  const authStore = useAuthStore();

  if (authStore.username === null && authStore.initializationError === null) {
    try {
      await authStore.checkAuthStatus();
    } catch (error) {
      // ...
    }
  }
  
  const requiresAuth = to.meta.requiresAuth;
  const isAuthEnabled = authStore.isAuthEnabled;
  const isLoggedIn = authStore.isLoggedIn;

  if (requiresAuth && isAuthEnabled && !isLoggedIn) {
    next({ name: 'Login' });
  } else {
    next();
  }
});


// --- 5. 导出路由实例 (这部分保持不变) ---
export default router;
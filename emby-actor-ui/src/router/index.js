// src/router/index.js

import { createRouter, createWebHistory } from 'vue-router';

// --- 1. 导入你的页面组件 ---
// 确保这些路径与你项目中文件的实际位置一致！

// 核心功能页面
import ReviewList from '../components/ReviewList.vue';
import ActionsPage from '../components/ActionsPage.vue';

// 设置相关的页面
import EmbySettingsPage from '../components/settings/EmbySettingsPage.vue';
import ApiDataSourceSettingsPage from '../components/settings/ApiDataSourceSettingsPage.vue';
import SchedulerSettingsPage from '../components/settings/SchedulerSettingsPage.vue';
import GeneralSettingsPage from '../components/settings/GeneralSettingsPage.vue';

// 我们为手动编辑媒体信息预留的页面组件 (假设你之后会创建它)
// 为了让路由能跑起来，我们可以先用一个简单的占位组件，或者直接用 ReviewList 暂时替代
// import MediaEditPage from '../components/MediaEditPage.vue'; // 假设的编辑页面

// --- 2. 定义路由规则 (你的“导航地图”) ---
const routes = [
  // 默认根路径重定向到待复核列表
  {
    path: '/',
    redirect: '/review'
  },
  {
    path: '/review',
    name: 'ReviewList', // 这个 name 需要和 App.vue 中菜单的 key 对应
    component: ReviewList
  },
  {
    path: '/actions',
    name: 'actions-status', // 对应菜单的 key
    component: ActionsPage
  },
  // 设置相关的页面可以组织在一个父路由下，也可以分开定义
  // 这里我们分开定义，与你 App.vue 菜单结构可能更匹配
  {
    path: '/settings/emby',
    name: 'settings-emby', // 对应菜单的 key
    component: EmbySettingsPage
  },
  {
    path: '/settings/api',
    name: 'settings-api', // 对应菜单的 key
    component: ApiDataSourceSettingsPage
  },
  {
    path: '/settings/scheduler',
    name: 'settings-scheduler', // 对应菜单的 key
    component: SchedulerSettingsPage
  },
  {
    path: '/settings/general',
    name: 'settings-general', // 对应菜单的 key
    component: GeneralSettingsPage
  },
  // --- 为“手动编辑媒体”功能预留的路由 ---
  // 它会接收一个 itemId 作为参数
  {
    path: '/edit-media/:itemId', // :itemId 表示这是一个动态参数
    name: 'MediaEditPage',      // 给这个路由起个名字
    component: () => import('../components/MediaEditPage.vue'), // 示例：使用路由懒加载导入组件
    // component: ReviewList, // 或者在 MediaEditPage.vue 创建好之前，先用一个已有的组件占位
    props: true // 这会将路由参数 (如 itemId) 作为 props 传递给 MediaEditPage 组件
  },
  // --- （可选）添加一个404页面 ---
  // {
  //   path: '/:catchAll(.*)*', // 匹配所有未匹配到的路径
  //   name: 'NotFound',
  //   component: () => import('../views/NotFound.vue') // 假设你有一个 NotFound.vue 页面
  // }
];

// --- 3. 创建路由实例 ---
const router = createRouter({
  // 使用 HTML5 History 模式 (URL中没有 #)
  // import.meta.env.BASE_URL 是 Vite 提供的环境变量，通常是 '/'
  history: createWebHistory(import.meta.env.BASE_URL),
  routes, // 将我们定义的路由规则列表传递给路由实例
  // (可选) 当路由切换时，页面滚动到顶部
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) {
      return savedPosition;
    } else {
      return { top: 0 };
    }
  }
});

// --- 4. 导出路由实例 ---
// 这样我们就可以在 main.js 中导入并使用它了
export default router;
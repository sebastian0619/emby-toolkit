<!-- src/App.vue -->
<template>
  <div :class="isDarkTheme ? 'dark-mode' : 'light-mode'">
    <n-config-provider :theme="isDarkTheme ? darkTheme : undefined" :theme-overrides="themeOverridesComputed" :locale="zhCN" :date-locale="dateZhCN">
      <n-message-provider>
        <n-dialog-provider>
        <!-- 1. 如果需要登录，则显示全屏的登录页面 -->
        <div v-if="authStore.isAuthEnabled && !authStore.isLoggedIn" class="fullscreen-container">
          <Login />
        </div>

        <!-- 2. 否则，显示后台管理布局 -->
        <n-layout v-else style="height: 100vh;">
          <n-layout-header :bordered="false" class="app-header">
            <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
              <span>Emby 演员/角色中文化</span>
                <div style="display: flex; align-items: center; gap: 16px;">
                  <!-- 用户名下拉菜单 -->
                  <n-dropdown 
                    v-if="authStore.isAuthEnabled" 
                    trigger="hover" 
                    :options="userOptions" 
                    @select="handleUserSelect"
                  >
                    <div style="display: flex; align-items: center; cursor: pointer; gap: 4px;">
                      <span style="font-size: 14px;">欢迎, {{ authStore.username }}</span>
                      <!-- 可以加一个下拉小箭头图标，更美观 -->
                      <svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 24 24"><path fill="currentColor" d="m7 10l5 5l5-5z"></path></svg>
                    </div>
                  </n-dropdown>

                  <span style="font-size: 12px; color: #999;">v{{ appVersion }}</span>
                  <n-switch v-model:value="isDarkTheme" size="small">
                    <template #checked>暗色</template>
                    <template #unchecked>亮色</template>
                  </n-switch>
                </div>
            </div>
          </n-layout-header>
          <n-layout has-sider>
            <n-layout-sider
              :bordered="false"
              collapse-mode="width"
              :collapsed-width="64"
              :width="240"
              show-trigger="arrow-circle"
              content-style="padding-top: 10px;"
              :native-scrollbar="false"
              :collapsed="collapsed"
              @update:collapsed="val => collapsed = val"
            >
              <n-menu
                :collapsed="collapsed"
                :collapsed-width="64"
                :collapsed-icon-size="22"
                :options="menuOptions"
                :value="activeMenuKey"
                @update:value="handleMenuUpdate"
              />
            </n-layout-sider>
            <n-layout-content
              class="app-main-content-wrapper"
              content-style="padding: 24px; transition: background-color 0.3s;"
              :style="{ backgroundColor: isDarkTheme ? (themeOverridesComputed?.common?.bodyColor || '#101014') : (themeOverridesComputed?.common?.bodyColor || '#f0f2f5') }"
              :native-scrollbar="false"
            >
              <div class="status-display-area" v-if="showStatusArea">
                <n-card size="small" :bordered="false" style="margin-bottom: 15px;">
                   <p style="margin: 0; font-size: 0.9em;">
                      <strong>任务状态:</strong>
                      <n-text :type="statusTypeComputed">{{ backgroundTaskStatus.current_action }}</n-text> -
                      <n-text :type="statusTypeComputed" :depth="2">{{ backgroundTaskStatus.message }}</n-text>
                      <n-progress
                          v-if="backgroundTaskStatus.is_running && backgroundTaskStatus.progress >= 0 && backgroundTaskStatus.progress < 100"
                          type="line"
                          :percentage="backgroundTaskStatus.progress"
                          :indicator-placement="'inside'"
                          processing
                          style="margin-top: 5px;"
                      />
                      <span v-else-if="backgroundTaskStatus.is_running"> ({{ backgroundTaskStatus.progress }}%)</span>
                   </p>
                </n-card>
              </div>
              <div class="page-content-inner-wrapper">
               <router-view v-slot="slotProps">
                <component :is="slotProps.Component" :task-status="backgroundTaskStatus" />
              </router-view>
              </div>
            </n-layout-content>
          </n-layout>
        </n-layout>
        <n-modal 
            v-model:show="showPasswordModal"
            preset="card"
            style="width: 90%; max-width: 500px;"
            title="修改密码"
            :bordered="false"
            size="huge"
          >
            <ChangePassword @password-changed="showPasswordModal = false" />
          </n-modal>
         </n-dialog-provider>
        </n-message-provider>
    </n-config-provider>
  </div>
</template>

<script setup>
import { ref, computed, h, onMounted, onBeforeUnmount, watch } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import {
  NConfigProvider, NLayout, NLayoutHeader, NLayoutSider, NLayoutContent,
  NMenu, NSwitch, NIcon, NCard, NText, NProgress,
  darkTheme, zhCN, dateZhCN, useMessage, NMessageProvider,
  NModal, NDropdown
} from 'naive-ui';
import axios from 'axios';
import { useAuthStore } from './stores/auth';
// [新增] 引入 useConfig 以获取全局配置
import { useConfig } from './composables/useConfig';
import Login from './components/Login.vue';
import ChangePassword from './components/settings/ChangePassword.vue';
import {
  PlayCircleOutline as ActionsIcon,
  ListOutline as ReviewListIcon,
  ServerOutline as EmbyIcon,
  KeyOutline as ApiIcon,
  TimerOutline as SchedulerIcon,
  OptionsOutline as GeneralIcon,
  LogOutOutline as LogoutIcon,
  HeartOutline as WatchlistIcon,
  AlbumsOutline as CollectionsIcon,
  PeopleOutline as ActorSubIcon,
} from '@vicons/ionicons5';
import { Password24Regular as PasswordIcon } from '@vicons/fluent'
import { watchEffect } from 'vue'
const router = useRouter(); 
const route = useRoute(); 
const authStore = useAuthStore();
// [新增] 调用 useConfig 获取配置模型
const { configModel } = useConfig();

// --- 状态定义 (Refs) ---
const showPasswordModal = ref(false);
const isDarkTheme = ref(localStorage.getItem('theme') !== 'light');
const collapsed = ref(false);
const backgroundTaskStatus = ref({ is_running: false, current_action: '空闲', message: '等待任务', progress: 0 });
const showStatusArea = ref(true);
const activeMenuKey = computed(() => route.name);
const appVersion = ref(__APP_VERSION__);

watch(isDarkTheme, (newValue) => {
  localStorage.setItem('theme', newValue ? 'dark' : 'light');
});

const renderIcon = (iconComponent) => {
  return () => h(NIcon, null, { default: () => h(iconComponent) });
};
watchEffect(() => {
  const html = document.documentElement
  html.classList.remove('dark', 'light')
  html.classList.add(isDarkTheme.value ? 'dark' : 'light')
})
// --- 用户下拉菜单的逻辑 ---
const userOptions = computed(() => [
  {
    label: '修改密码',
    key: 'change-password',
    icon: renderIcon(PasswordIcon)
  },
  {
    label: '退出登录',
    key: 'logout',
    icon: renderIcon(LogoutIcon)
  }
]);

const handleUserSelect = async (key) => {
  if (key === 'change-password') {
    showPasswordModal.value = true;
  } else if (key === 'logout') {
    await authStore.logout();
    router.push({ name: 'Login' });
  }
};

// --- [修改] 侧边栏菜单的定义，使其动态化 ---
const menuOptions = computed(() => [
  { label: 'Emby 配置', key: 'settings-emby', icon: renderIcon(EmbyIcon) },
  { label: '通用设置', key: 'settings-general', icon: renderIcon(GeneralIcon) },
  { type: 'divider', key: 'd1' },
  { label: '任务中心', key: 'actions-status', icon: renderIcon(ActionsIcon) },
  { label: '合集检查', key: 'Collections', icon: renderIcon(CollectionsIcon) },
  { label: '智能追剧', key: 'Watchlist', icon: renderIcon(WatchlistIcon) },
  { label: '演员订阅', key: 'ActorSubscriptions', icon: renderIcon(ActorSubIcon) },
  { label: '手动处理', key: 'ReviewList', icon: renderIcon(ReviewListIcon) },
  { label: '定时任务', key: 'settings-scheduler', icon: renderIcon(SchedulerIcon) },
]);


// --- 菜单点击事件处理 ---
async function handleMenuUpdate(key) {
  router.push({ name: key });
}

const statusTypeComputed = computed(() => 'info');

const fetchStatus = async () => {
  try {
    const response = await axios.get('/api/status');
    backgroundTaskStatus.value = response.data;
  } catch (error) {
    if (error.response?.status !== 401) {
      console.error('获取状态失败:', error);
    }
  }
};

const themeOverridesComputed = computed(() => {
  const lightCardShadow = '0 1px 2px -2px rgba(0, 0, 0, 0.08), 0 3px 6px 0 rgba(0, 0, 0, 0.06), 0 5px 12px 4px rgba(0, 0, 0, 0.04)';
  const darkCardShadow = '0 1px 2px -2px rgba(0, 0, 0, 0.24), 0 3px 6px 0 rgba(0, 0, 0, 0.18), 0 5px 12px 4px rgba(0, 0, 0, 0.12)';

  if (!isDarkTheme.value) {
    return {
      common: { bodyColor: '#f0f2f5' },
      Card: { boxShadow: lightCardShadow }
    };
  }
  
  return {
    common: { 
      bodyColor: '#101014', 
      cardColor: '#1a1a1e', 
      inputColor: '#1a1a1e', 
      actionColor: '#242428', 
      borderColor: 'rgba(255, 255, 255, 0.12)' 
    },
    Card: { 
      color: '#1a1a1e', 
      titleTextColor: 'rgba(255, 255, 255, 0.92)',
      boxShadow: darkCardShadow,
    },
    DataTable: { 
      tdColor: '#1a1a1e', 
      thColor: '#1a1a1e', 
      tdColorStriped: '#202024' 
    },
    Input: { color: '#1a1a1e' },
    Select: { peers: { InternalSelection: { color: '#1a1a1e' } } }
  };
});

let statusIntervalId = null;

onMounted(() => {
  if (authStore.isLoggedIn) {
    fetchStatus();
    statusIntervalId = setInterval(fetchStatus, 1000);
  }
});

onBeforeUnmount(() => {
  if (statusIntervalId) clearInterval(statusIntervalId);
});

watch(() => authStore.isLoggedIn, (newIsLoggedIn) => {
  if (newIsLoggedIn) {
    if (!statusIntervalId) {
      fetchStatus();
      statusIntervalId = setInterval(fetchStatus, 1000);
    }
  } else {
    if (statusIntervalId) {
      clearInterval(statusIntervalId);
      statusIntervalId = null;
    }
  }
});
</script>

<style>
html, body { height: 100vh; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; overflow: hidden; }
.app-header { padding: 0 24px; height: 60px; display: flex; align-items: center; font-size: 1.25em; font-weight: 600; flex-shrink: 0; }
.status-display-area .n-card .n-card__content { padding: 8px 12px !important; }
.status-display-area p { margin: 0; font-size: 0.9em; }
.app-main-content-wrapper { height: 100%; display: flex; flex-direction: column; }
.status-display-area { flex-shrink: 0; }
.page-content-inner-wrapper { flex-grow: 1; overflow-y: auto; }
.fullscreen-container { display: flex; justify-content: center; align-items: center; height: 100vh; width: 100%; background-color: #f0f2f5; }
.dark-mode .fullscreen-container { background-color: #101014; }
</style>
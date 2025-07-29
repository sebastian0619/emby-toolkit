<!-- src/App.vue -->
<template>
  <div :class="isDarkTheme ? 'dark-mode' : 'light-mode'">
    <div v-if="authStore.isAuthEnabled && !authStore.isLoggedIn" class="fullscreen-container">
      <Login />
    </div>
    <n-layout v-else style="height: 100vh;">
      <n-layout-header :bordered="false" class="app-header">
        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
          <span>Emby 演员/角色中文化</span>
            <div style="display: flex; align-items: center; gap: 16px;">
              <n-dropdown 
                v-if="authStore.isAuthEnabled" 
                trigger="hover" 
                :options="userOptions" 
                @select="handleUserSelect"
              >
                <div style="display: flex; align-items: center; cursor: pointer; gap: 4px;">
                  <span style="font-size: 14px;">欢迎, {{ authStore.username }}</span>
                  <svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 24 24"><path fill="currentColor" d="m7 10l5 5l5-5z"></path></svg>
                </div>
              </n-dropdown>

              <!-- ★★★ 更新按钮已从此区域移除 ★★★ -->
              <div style="display: flex; align-items: center; gap: 8px;">
                <router-link to="/releases" custom v-slot="{ navigate }">
                  <span @click="navigate" style="font-size: 12px; color: #999; cursor: pointer;" title="查看版本详情">
                    v{{ appVersion }}
                  </span>
                </router-link>
              </div>

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
  </div>
</template>

<script setup>
import { ref, computed, h, onMounted, onBeforeUnmount, watch } from 'vue';
import { useRouter, useRoute } from 'vue-router';
// ★★★ 移除了 NButton 和 NModal 的导入 ★★★
import {
  NLayout, NLayoutHeader, NLayoutSider, NLayoutContent,
  NMenu, NSwitch, NIcon, NCard, NText, NProgress,
  NModal, NDropdown
} from 'naive-ui';
import axios from 'axios';
import { useAuthStore } from './stores/auth';
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
  InformationCircleOutline as AboutIcon,
} from '@vicons/ionicons5';
import { Password24Regular as PasswordIcon } from '@vicons/fluent'
// ★★★ 不再需要导入 appStore ★★★
// import { useAppStore } from './stores/app';

const appVersion = ref(__APP_VERSION__);
const router = useRouter(); 
const route = useRoute(); 
const authStore = useAuthStore();

const showPasswordModal = ref(false);
const isDarkTheme = ref(localStorage.getItem('theme') !== 'light');
const collapsed = ref(false);
const backgroundTaskStatus = ref({ is_running: false, current_action: '空闲', message: '等待任务', progress: 0, last_action: null });
const showStatusArea = ref(true);
const activeMenuKey = computed(() => route.name);

// ★★★ 所有与更新相关的状态和方法都已移除 ★★★
let eventSource = null;

watch(isDarkTheme, (newValue) => {
  localStorage.setItem('theme', newValue ? 'dark' : 'light');
});

const renderIcon = (iconComponent) => {
  return () => h(NIcon, null, { default: () => h(iconComponent) });
};

const userOptions = computed(() => [
  { label: '修改密码', key: 'change-password', icon: renderIcon(PasswordIcon) },
  { label: '退出登录', key: 'logout', icon: renderIcon(LogoutIcon) }
]);

const handleUserSelect = async (key) => {
  if (key === 'change-password') {
    showPasswordModal.value = true;
  } else if (key === 'logout') {
    await authStore.logout();
    router.push({ name: 'Login' });
  }
};

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
  { label: '查看更新', key: 'Releases', icon: renderIcon(AboutIcon) },
]);

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

let statusIntervalId = null;

onMounted(() => {
  // ★★★ 全局版本检查已移除 ★★★
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
/* 样式保持不变 */
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
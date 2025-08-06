<!-- src/components/MainLayout.vue (最终正确版) -->
<template>
  <n-layout style="height: 100vh;">
    <n-layout-header :bordered="false" class="app-header">
      <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
        <span>Emby 工具箱</span>
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
                <svg xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" viewBox="0 0 24 24"><path fill="currentColor" d="m7 10l5 5l5-5z"></path></svg>
              </div>
            </n-dropdown>

            <span style="font-size: 12px; color: #999;">v{{ appVersion }}</span>
            <n-switch 
              :value="props.isDark" 
              @update:value="newValue => emit('update:is-dark', newValue)"
              size="small"
            >
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
            <p style="margin: 0; font-size: 0.9em; display: flex; align-items: center; justify-content: space-between; gap: 16px;">
              <span style="flex-grow: 1;">
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
              </span>
              <n-button
                v-if="backgroundTaskStatus.is_running"
                type="error"
                size="small"
                @click="triggerStopTask"
                ghost
              >
                <template #icon><n-icon :component="StopIcon" /></template>
              </n-button>
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
  </n-layout>
</template>

<script setup>
import { ref, computed, h, onMounted, onBeforeUnmount, watch } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import {
  NLayout, NLayoutHeader, NLayoutSider, NLayoutContent,
  NMenu, NSwitch, NIcon, NCard, NText, NProgress,
  useMessage, NModal, NDropdown, NButton
} from 'naive-ui';
import axios from 'axios';
import { useAuthStore } from './stores/auth';
import { useConfig } from './composables/useConfig';
import ChangePassword from './components/settings/ChangePassword.vue';
import {
  AnalyticsOutline as StatsIcon,
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
  CreateOutline as CustomCollectionsIcon,
  ColorPaletteOutline as PaletteIcon,
  Stop as StopIcon
} from '@vicons/ionicons5';
import { Password24Regular as PasswordIcon } from '@vicons/fluent';

// 1. 定义接收的 props 和要发出的 emits
const props = defineProps({
  isDark: Boolean
});
const emit = defineEmits(['update:is-dark']);

// 2. 内部状态定义
const router = useRouter(); 
const route = useRoute(); 
const authStore = useAuthStore();
const { configModel } = useConfig();
const message = useMessage();

const showPasswordModal = ref(false);
const collapsed = ref(false);
const backgroundTaskStatus = ref({ is_running: false, current_action: '空闲', message: '等待任务', progress: 0, last_action: null });
const showStatusArea = ref(true);
const activeMenuKey = computed(() => route.name);
const appVersion = ref(__APP_VERSION__);

// 3. 所有函数和 computed 属性
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
  }
};

const menuOptions = computed(() => [
  { label: '发现', key: 'group-discovery', type: 'group', children: [ { label: '数据看板', key: 'DatabaseStats', icon: renderIcon(StatsIcon) } ] },
  { label: '整理', key: 'group-management', type: 'group', children: [ { label: '原生合集', key: 'Collections', icon: renderIcon(CollectionsIcon) }, { label: '自建合集', key: 'CustomCollectionsManager', icon: renderIcon(CustomCollectionsIcon) }, { label: '封面生成', key: 'CoverGeneratorConfig', icon: renderIcon(PaletteIcon) }, { label: '手动处理', key: 'ReviewList', icon: renderIcon(ReviewListIcon) }, ] },
  { label: '订阅', key: 'group-subscriptions', type: 'group', children: [ { label: '智能追剧', key: 'Watchlist', icon: renderIcon(WatchlistIcon) }, { label: '演员订阅', key: 'ActorSubscriptions', icon: renderIcon(ActorSubIcon) }, ] },
  { label: '系统', key: 'group-system', type: 'group', children: [ { label: '通用设置', key: 'settings-general', icon: renderIcon(GeneralIcon) }, { label: '任务中心', key: 'settings-scheduler', icon: renderIcon(SchedulerIcon) }, { label: '查看更新', key: 'Releases', icon: renderIcon(AboutIcon) }, ] }
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

const triggerStopTask = async () => {
  try {
    await axios.post('/api/trigger_stop_task');
    message.info('已发送停止任务请求。');
  } catch (error) {
    message.error(error.response?.data?.error || '发送停止任务请求失败，请查看日志。');
  }
};

let statusIntervalId = null;

onMounted(() => {});
onBeforeUnmount(() => {
  if (statusIntervalId) clearInterval(statusIntervalId);
});

watch(() => authStore.isLoggedIn, (newIsLoggedIn, oldIsLoggedIn) => {
  if (newIsLoggedIn) {
    if (!oldIsLoggedIn && authStore.forceChangePassword) {
      showPasswordModal.value = true;
    }
    if (!statusIntervalId) {
      fetchStatus();
      statusIntervalId = setInterval(fetchStatus, 1000);
    }
  } else {
    if (statusIntervalId) {
      clearInterval(statusIntervalId);
      statusIntervalId = null;
    }
    if (route.name !== 'Login') {
      router.push({ name: 'Login' });
    }
  }
}, { immediate: true });
</script>

<style>
/* MainLayout 的样式 */
.app-header { padding: 0 24px; height: 60px; display: flex; align-items: center; font-size: 1.25em; font-weight: 600; flex-shrink: 0; }
.status-display-area .n-card .n-card__content { padding: 8px 12px !important; }
.status-display-area p { margin: 0; font-size: 0.9em; }
.app-main-content-wrapper { height: 100%; display: flex; flex-direction: column; }
.status-display-area { flex-shrink: 0; }
.page-content-inner-wrapper { flex-grow: 1; overflow-y: auto; }
.n-menu .n-menu-item-group-title { font-size: 12px; font-weight: 500; color: #8e8e93; padding-left: 24px; margin-top: 16px; margin-bottom: 8px; }
.n-menu .n-menu-item-group:first-child .n-menu-item-group-title { margin-top: 0; }
html.dark .n-menu .n-menu-item-group-title { color: #828287; }
</style>
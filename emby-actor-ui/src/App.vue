<template>
  <n-config-provider :theme="isDarkTheme ? darkTheme : undefined" :theme-overrides="themeOverridesComputed" :locale="zhCN" :date-locale="dateZhCN">
        <n-layout style="height: 100vh;">
          <n-layout-header :bordered="false" class="app-header">
            <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
              <span>Emby 演员管理</span>
                <div style="display: flex; align-items: center; gap: 12px;">
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
                key-field="key"
                label-field="label"
                children-field="children"
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
  </n-config-provider>
</template>

<script setup>
import { ref, computed, h, onMounted, onBeforeUnmount } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import {
  NConfigProvider,
  NLayout, NLayoutHeader, NLayoutSider, NLayoutContent,
  NMenu, NSwitch, NIcon, NCard, NText, NProgress,
  darkTheme, zhCN, dateZhCN, useMessage
} from 'naive-ui';
import axios from 'axios';
import { useConfig } from './composables/useConfig.js';

// 导入新的独立设置页面组件
import EmbySettingsPage from './components/settings/EmbySettingsPage.vue';
import ApiDataSourceSettingsPage from './components/settings/ApiDataSourceSettingsPage.vue';
import SchedulerSettingsPage from './components/settings/SchedulerSettingsPage.vue';
import GeneralSettingsPage from './components/settings/GeneralSettingsPage.vue';
// 其他页面组件
import ActionsPage from './components/ActionsPage.vue';
import ReviewList from './components/ReviewList.vue';

import {
  PlayCircleOutline as ActionsIcon,
  ListOutline as ReviewListIcon,
  ServerOutline as EmbyIcon,
  KeyOutline as ApiIcon,
  TimerOutline as SchedulerIcon,
  OptionsOutline as GeneralIcon
} from '@vicons/ionicons5';
const router = useRouter(); 
const route = useRoute(); 
const isDarkTheme = ref(true);
const collapsed = ref(false);
const currentComponentKey = ref('settings-emby');

const backgroundTaskStatus = ref({ is_running: false, current_action: '空闲', message: '等待任务', progress: 0 });
const showStatusArea = ref(true);
const activeMenuKey = computed(() => route.name);
const appVersion = ref(__APP_VERSION__);

const renderIcon = (iconComponent) => {
  return () => h(NIcon, null, { default: () => h(iconComponent) });
};

const menuOptions = ref([
  { label: 'Emby 配置', key: 'settings-emby', icon: renderIcon(EmbyIcon) },
  { label: '数据源', key: 'settings-api', icon: renderIcon(ApiIcon) },
  { label: '定时任务', key: 'settings-scheduler', icon: renderIcon(SchedulerIcon) },
  { label: '通用设置', key: 'settings-general', icon: renderIcon(GeneralIcon) },
  { type: 'divider', key: 'd1' },
  { label: '全量处理', key: 'actions-status', icon: renderIcon(ActionsIcon) },
  { label: '手动处理', key: 'ReviewList', icon: renderIcon(ReviewListIcon) },
]);

function handleMenuUpdate(key, item) { // item 是被点击的菜单项对象
  // 假设菜单的 key 就是路由的 name
  // 或者如果 key 是路径，可以直接 router.push(key)
  if (item.path) { // 如果你的菜单项里直接存了 path
      router.push(item.path);
  } else { // 否则假设 key 是路由的 name
      router.push({ name: key });
  }
}

const statusTypeComputed = computed(() => { return 'info';});
const appMessage = useMessage(); // App.vue 自身的 message 实例
const { fetchConfigData: initialFetchGlobalConfig, configError: globalConfigError } = useConfig();

const fetchStatus = async () => {
  try {
    const response = await axios.get('/api/status');
    backgroundTaskStatus.value = response.data;
  } catch (error) {
    console.error('获取状态失败:', error);
  }
};
const themeOverridesComputed = computed(() => {
  if (isDarkTheme.value) {
    return { common: { bodyColor: '#18181c' }};
  }
  return { common: { bodyColor: '#f0f2f5' }};
});
let statusIntervalId = null;

onMounted(async () => {
  await initialFetchGlobalConfig();
  if (globalConfigError.value && appMessage) { // 检查 useConfig 返回的错误状态
    appMessage.error(`初始化应用配置失败: ${globalConfigError.value}`);
  }
  await fetchStatus();
  statusIntervalId = setInterval(fetchStatus, 1000);
});

onBeforeUnmount(() => {
  if (statusIntervalId) {
    clearInterval(statusIntervalId);
  }
});

</script>

<style>
html, body {
  height: 100vh;
  margin: 0;
  padding: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  overflow: hidden;
}
.app-header {
  padding: 0 24px;
  height: 60px;
  display: flex;
  align-items: center;
  font-size: 1.25em;
  font-weight: 600;
  flex-shrink: 0;
}
.status-display-area .n-card .n-card__content {
    padding: 8px 12px !important;
}
.status-display-area p {
    margin: 0;
    font-size: 0.9em;
}
.app-main-content-wrapper {
    height: 100%;
    display: flex;
    flex-direction: column;
}
.status-display-area {
    flex-shrink: 0;
}
.page-content-inner-wrapper {
    flex-grow: 1;
    overflow-y: auto;
}
</style>
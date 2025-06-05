<!-- src/AppShell.vue -->
<template>
  <n-config-provider :theme="isDarkTheme ? darkTheme : undefined" :theme-overrides="themeOverridesComputed" :locale="zhCN" :date-locale="dateZhCN">
    <n-layout style="height: 100vh;">
      <!-- ... header ... -->
      <n-layout-header bordered class="app-header">
        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
          <span>Emby 演员处理工具</span>
          <n-switch v-model:value="isDarkTheme" size="small">
            <template #checked>暗色</template>
            <template #unchecked>亮色</template>
          </n-switch>
        </div>
      </n-layout-header>
      <n-layout has-sider>
        <n-layout-sider
          bordered
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
            :default-expanded-keys="['settings-group']" 
          />
        </n-layout-sider>
        <n-layout-content
          class="app-main-content-wrapper"
          content-style="padding: 24px; transition: background-color 0.3s;"
          :style="{ backgroundColor: isDarkTheme.value ? (themeOverridesComputed?.common?.bodyColor || '#101014') : (themeOverridesComputed?.common?.bodyColor || '#f0f2f5') }"
          :native-scrollbar="false"
        >
          <div class="status-display-area" v-if="showStatusArea">
            <!-- ... status content ... -->
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
             <component
                :is="activeComponent"
                :is-task-running="backgroundTaskStatus.is_running"
                :initial-tab="settingsPageInitialTab" 
              />
          </div>
        </n-layout-content>
      </n-layout>
    </n-layout>
  </n-config-provider>
</template>

<script setup>
import { ref, computed, h, onMounted, onBeforeUnmount } from 'vue';
// ... 其他 imports ...
import {
  NConfigProvider,
  NLayout, NLayoutHeader, NLayoutSider, NLayoutContent,
  NMenu, NSwitch, NIcon, NCard, NText, NProgress,
  darkTheme, zhCN, dateZhCN, useMessage
} from 'naive-ui';
import axios from 'axios';
import { useConfig } from './composables/useConfig.js';
import SettingsPage from './components/SettingsPage.vue';
import ActionsPage from './components/ActionsPage.vue';
import ReviewList from './components/ReviewList.vue';
import {
  SettingsOutline as SettingsIcon,
  PlayCircleOutline as ActionsIcon,
  ListOutline as ReviewListIcon,
  ServerOutline as EmbyIcon,
  KeyOutline as ApiIcon,
  TimerOutline as SchedulerIcon,
  OptionsOutline as GeneralIcon
} from '@vicons/ionicons5';


const isDarkTheme = ref(true);
const collapsed = ref(false);
const currentComponentKey = ref('settings-emby'); // 默认页面
const activeMenuKey = computed(() => currentComponentKey.value);

const backgroundTaskStatus = ref({ /* ... */ });
const showStatusArea = ref(true);

// 组件映射
const componentsMap = {
  'actions-status': ActionsPage,
  'review-list': ReviewList,
};

const activeComponent = computed(() => {
  if (currentComponentKey.value.startsWith('settings-')) {
    return SettingsPage;
  }
  return componentsMap[currentComponentKey.value] || SettingsPage; // 兜底
});

// 计算传递给 SettingsPage 的 initialTab prop
const settingsPageInitialTab = computed(() => {
  if (currentComponentKey.value.startsWith('settings-')) {
    return currentComponentKey.value.replace('settings-', ''); // "settings-emby" -> "emby"
  }
  return 'emby'; // 默认值，如果不是 settings 页面，或者 key 不匹配
});

const renderIcon = (iconComponent) => {
  return () => h(NIcon, null, { default: () => h(iconComponent) });
};

const menuOptions = ref([
  {
    label: '应用配置',
    key: 'settings-group',
    icon: renderIcon(SettingsIcon),
    children: [
      { label: 'Emby 配置', key: 'settings-emby', icon: renderIcon(EmbyIcon) },
      { label: 'API & 数据源', key: 'settings-api', icon: renderIcon(ApiIcon) },
      { label: '定时任务', key: 'settings-scheduler', icon: renderIcon(SchedulerIcon) },
      { label: '通用设置', key: 'settings-general', icon: renderIcon(GeneralIcon) },
    ]
  },
  { type: 'divider', key: 'd1' },
  { label: '手动操作', key: 'actions-status', icon: renderIcon(ActionsIcon) },
  { label: '待复核列表', key: 'review-list', icon: renderIcon(ReviewListIcon) },
]);

function handleMenuUpdate(key, item) {
  if (key !== 'settings-group') {
      currentComponentKey.value = key;
  }
}

const statusTypeComputed = computed(() => { /* ... */ });
const appMessage = useMessage();
const fetchStatus = async () => { /* ... */ };
const themeOverridesComputed = computed(() => { /* ... */ });
let statusIntervalId = null;
const { fetchConfigData: initialFetchGlobalConfig } = useConfig();

onMounted(async () => {
  try {
    await initialFetchGlobalConfig();
  } catch (error) {
    console.error("初始化应用配置失败:", error);
    if (appMessage) {
        appMessage.error("初始化应用配置失败，请检查后端服务或网络。");
    }
  }
  await fetchStatus();
  statusIntervalId = setInterval(fetchStatus, 3000);
});

onBeforeUnmount(() => {
  if (statusIntervalId) {
    clearInterval(statusIntervalId);
  }
});

// 省略 backgroundTaskStatus, showStatusArea, statusTypeComputed, fetchStatus, themeOverridesComputed 的具体实现
// 它们与之前保持一致
</script>

<style>
/* ... 样式保持不变 ... */
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
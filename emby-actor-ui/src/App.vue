<template>
  <!-- 
    ★★★ 1. 在 n-config-provider 外层包裹一个 div ★★★
    这个 div 将作为我们应用主题 class 的根节点，确保 Naive UI 能正确应用主题。
  -->
  <div :class="isDarkTheme ? 'dark-mode' : 'light-mode'">
    <n-config-provider :theme="isDarkTheme ? darkTheme : undefined" :theme-overrides="themeOverridesComputed" :locale="zhCN" :date-locale="dateZhCN">
      <n-message-provider> <!-- ★★★ (可选但推荐) 包裹 n-message-provider ★★★ -->
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
      </n-message-provider>
    </n-config-provider>
  </div>
</template>

<script setup>
// ★★★ 2. 确保导入了 watch ★★★
import { ref, computed, h, onMounted, onBeforeUnmount, watch } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import {
  NConfigProvider,
  NLayout, NLayoutHeader, NLayoutSider, NLayoutContent,
  NMenu, NSwitch, NIcon, NCard, NText, NProgress,
  darkTheme, zhCN, dateZhCN, useMessage, NMessageProvider // ★★★ 确保导入了 NMessageProvider ★★★
} from 'naive-ui';
import axios from 'axios';
import { useConfig } from './composables/useConfig.js';

import EmbySettingsPage from './components/settings/EmbySettingsPage.vue';
import ApiDataSourceSettingsPage from './components/settings/ApiDataSourceSettingsPage.vue';
import SchedulerSettingsPage from './components/settings/SchedulerSettingsPage.vue';
import GeneralSettingsPage from './components/settings/GeneralSettingsPage.vue';
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

// ★★★ 3. 从 localStorage 初始化 isDarkTheme ★★★
const isDarkTheme = ref(localStorage.getItem('theme') !== 'light');

const collapsed = ref(false);
const backgroundTaskStatus = ref({ is_running: false, current_action: '空闲', message: '等待任务', progress: 0 });
const showStatusArea = ref(true);
const activeMenuKey = computed(() => route.name);
const appVersion = ref(__APP_VERSION__);

// ★★★ 4. 使用 watch 监听并保存主题变化 ★★★
watch(isDarkTheme, (newValue) => {
  if (newValue) {
    localStorage.setItem('theme', 'dark');
  } else {
    localStorage.setItem('theme', 'light');
  }
});

const renderIcon = (iconComponent) => {
  return () => h(NIcon, null, { default: () => h(iconComponent) });
};

const menuOptions = ref([
  { label: 'Emby 配置', key: 'settings-emby', icon: renderIcon(EmbyIcon) },
  { label: '数据源', key: 'settings-api', icon: renderIcon(ApiIcon) },
  { label: '通用设置', key: 'settings-general', icon: renderIcon(GeneralIcon) },
  { type: 'divider', key: 'd1' },
  { label: '全量处理', key: 'actions-status', icon: renderIcon(ActionsIcon) },
  { label: '手动处理', key: 'ReviewList', icon: renderIcon(ReviewListIcon) },
  { label: '定时任务', key: 'settings-scheduler', icon: renderIcon(SchedulerIcon) },
]);

function handleMenuUpdate(key, item) {
  if (item.path) {
      router.push(item.path);
  } else {
      router.push({ name: key });
  }
}

const statusTypeComputed = computed(() => { return 'info';});
const appMessage = useMessage();
const { fetchConfigData: initialFetchGlobalConfig, configError: globalConfigError } = useConfig();

const fetchStatus = async () => {
  try {
    const response = await axios.get('/api/status');
    backgroundTaskStatus.value = response.data;
  } catch (error) {
    console.error('获取状态失败:', error);
  }
};

// ★★★ 5. 增强版的主题变量配置 ★★★
const themeOverridesComputed = computed(() => {
  if (!isDarkTheme.value) {
    return {
      common: { bodyColor: '#f0f2f5' }
    };
  }
  return {
    common: {
      bodyColor: '#101014', 
      cardColor: '#1a1a1e', 
      inputColor: '#1a1a1e',
      actionColor: '#242428',
      borderColor: 'rgba(255, 255, 255, 0.12)',
    },
    Card: {
      color: '#1a1a1e',
      titleTextColor: 'rgba(255, 255, 255, 0.92)',
    },
    DataTable: {
      tdColor: '#1a1a1e',
      thColor: '#1a1a1e',
      tdColorStriped: '#202024',
    },
    Input: {
      color: '#1a1a1e',
    },
    Select: { // 顺便把 Select 的颜色也统一了
      peers: {
        InternalSelection: {
          color: '#1a1a1e'
        }
      }
    }
  };
});

let statusIntervalId = null;

onMounted(async () => {
  await initialFetchGlobalConfig();
  if (globalConfigError.value && appMessage) {
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
/* ... 你的全局样式保持不变 ... */
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
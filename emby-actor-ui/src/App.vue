<!-- src/App.vue (中央集权版) -->
<template>
  <div>
    <!-- themeOverrides 现在由我们的主题中心动态提供 -->
    <n-config-provider :theme="isDarkTheme ? darkTheme : undefined" :theme-overrides="currentNaiveTheme" :locale="zhCN" :date-locale="dateZhCN">
      <n-message-provider>
        <n-dialog-provider>
          <div v-if="authStore.isAuthEnabled && !authStore.isLoggedIn" class="fullscreen-container">
            <Login />
          </div>
          <!-- 将主题状态作为 props 传递给 MainLayout -->
          <MainLayout 
            v-else 
            :is-dark="isDarkTheme" 
            :selected-theme="selectedTheme"
            :task-status="backgroundTaskStatus"
            @update:is-dark="newValue => isDarkTheme = newValue"
            @update:selected-theme="newValue => selectedTheme = newValue"
          />
        </n-dialog-provider>
      </n-message-provider>
    </n-config-provider>
  </div>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'; // 引入 onBeforeUnmount
import { NConfigProvider, NMessageProvider, NDialogProvider, darkTheme, zhCN, dateZhCN } from 'naive-ui';
import { useAuthStore } from './stores/auth';
import MainLayout from './MainLayout.vue';
import Login from './components/Login.vue';
import { themes } from './theme.js';
import axios from 'axios'; // 【新增】导入 axios

const authStore = useAuthStore();

// --- 主题状态管理 (保持不变) ---
const isDarkTheme = ref(localStorage.getItem('isDark') === 'true');
const selectedTheme = ref(localStorage.getItem('user-theme') || 'default');
const currentNaiveTheme = ref({});

// --- 【新增】任务状态管理 ---
const backgroundTaskStatus = ref({ is_running: false, current_action: '空闲', message: '等待任务', progress: 0, last_action: null });
let statusIntervalId = null;

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

// --- 统一的状态监听与应用 ---
watch(
  [isDarkTheme, selectedTheme], 
  ([isDark, themeValue], [wasDark, oldThemeValue]) => {
    // ... 主题切换的“卸妆上妆”逻辑保持不变 ...
    const root = document.documentElement;
    if (oldThemeValue) {
      const oldThemeMode = wasDark ? 'dark' : 'light';
      const oldThemeConfig = themes[oldThemeValue]?.[oldThemeMode];
      if (oldThemeConfig && oldThemeConfig.custom) {
        for (const key in oldThemeConfig.custom) {
          root.style.removeProperty(key);
        }
      }
    }
    const themeMode = isDark ? 'dark' : 'light';
    const themeConfig = themes[themeValue]?.[themeMode];
    if (!themeConfig) return;
    currentNaiveTheme.value = themeConfig.naive;
    const customVars = themeConfig.custom;
    for (const key in customVars) {
      root.style.setProperty(key, customVars[key]);
    }
    root.classList.remove('dark', 'light');
    root.classList.add(isDark ? 'dark' : 'light');
    localStorage.setItem('isDark', String(isDark));
    localStorage.setItem('user-theme', themeValue);
}, { immediate: true });

// 【新增】监听登录状态，来启动或停止任务轮询
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
}, { immediate: true });

// 【新增】组件卸载前，清理定时器
onBeforeUnmount(() => {
  if (statusIntervalId) clearInterval(statusIntervalId);
});
</script>

<style>
/* 全局样式可以保留在 App.vue */
html, body { height: 100vh; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; overflow: hidden; }
.fullscreen-container { display: flex; justify-content: center; align-items: center; height: 100vh; width: 100%; }

/* 确保全局样式能正确应用 */
html.light .fullscreen-container { background-color: #f0f2f5; }
html.dark .fullscreen-container { background-color: #101014; }
</style>
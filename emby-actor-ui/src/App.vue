<!-- src/App.vue (最终正确版) -->
<template>
  <div>
    <n-config-provider :theme="isDarkTheme ? darkTheme : undefined" :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
      <n-message-provider>
        <n-dialog-provider>
          <!-- 1. 如果需要登录，则显示全屏的登录页面 -->
          <div v-if="authStore.isAuthEnabled && !authStore.isLoggedIn" class="fullscreen-container">
            <Login />
          </div>
          <!-- 2. 否则，渲染我们的主布局组件 -->
          <MainLayout 
            v-else 
            :is-dark="isDarkTheme" 
            @update:is-dark="newValue => isDarkTheme = newValue"
          />
        </n-dialog-provider>
      </n-message-provider>
    </n-config-provider>
  </div>
</template>

<script setup>
import { ref, computed, watchEffect } from 'vue';
import { NConfigProvider, NMessageProvider, NDialogProvider, darkTheme, zhCN, dateZhCN } from 'naive-ui';
import { useAuthStore } from './stores/auth';
import MainLayout from './MainLayout.vue';
import Login from './components/Login.vue';

const authStore = useAuthStore();

// 1. isDarkTheme 状态由 App.vue 这个顶层组件拥有和管理
const isDarkTheme = ref(localStorage.getItem('theme') !== 'light');

// 2. watchEffect 监听 isDarkTheme 的变化，并同步更新 <html> 标签的 class
watchEffect(() => {
  const html = document.documentElement;
  html.classList.remove('dark', 'light');
  html.classList.add(isDarkTheme.value ? 'dark' : 'light');
  localStorage.setItem('theme', isDarkTheme.value ? 'dark' : 'light');
});

// 3. 主题覆盖配置依赖 isDarkTheme，所以也必须在这里
const themeOverrides = computed(() => {
  const lightCardShadow = '0 1px 2px -2px rgba(0, 0, 0, 0.08), 0 3px 6px 0 rgba(0, 0, 0, 0.06), 0 5px 12px 4px rgba(0, 0, 0, 0.04)';
  const darkCardShadow = '0 1px 2px -2px rgba(0, 0, 0, 0.24), 0 3px 6px 0 rgba(0, 0, 0, 0.18), 0 5px 12px 4px rgba(0, 0, 0, 0.12)';

  if (!isDarkTheme.value) {
    return {
      common: { bodyColor: '#f0f2f5' },
      Card: { boxShadow: lightCardShadow }
    };
  }
  
  return {
    common: { bodyColor: '#101014', cardColor: '#1a1a1e', inputColor: '#1a1a1e', actionColor: '#242428', borderColor: 'rgba(255, 255, 255, 0.12)' },
    Card: { color: '#1a1a1e', titleTextColor: 'rgba(255, 255, 255, 0.92)', boxShadow: darkCardShadow, },
    DataTable: { tdColor: '#1a1a1e', thColor: '#1a1a1e', tdColorStriped: '#202024' },
    Input: { color: '#1a1a1e' },
    Select: { peers: { InternalSelection: { color: '#1a1a1e' } } }
  };
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
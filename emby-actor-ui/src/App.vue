<!-- src/App.vue (简化后的版本) -->
<template>
  <div :class="isDarkTheme ? 'dark-mode' : 'light-mode'">
    <n-config-provider :theme="isDarkTheme ? darkTheme : undefined" :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
      <n-message-provider>
        <n-dialog-provider>
          <!-- 1. 如果需要登录，则显示全屏的登录页面 -->
          <div v-if="authStore.isAuthEnabled && !authStore.isLoggedIn" class="fullscreen-container">
            <Login />
          </div>
          <!-- 2. 否则，渲染我们的主布局组件 -->
          <MainLayout v-else />
        </n-dialog-provider>
      </n-message-provider>
    </n-config-provider>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { NConfigProvider, NMessageProvider, NDialogProvider, darkTheme, zhCN, dateZhCN } from 'naive-ui';
import { useAuthStore } from './stores/auth';
import MainLayout from './MainLayout.vue'; // 引入新的布局组件
import Login from './components/Login.vue';

const authStore = useAuthStore();
const isDarkTheme = ref(localStorage.getItem('theme') !== 'light');

// 监听主题变化并更新 localStorage 和 html class
watch(isDarkTheme, (newValue) => {
  localStorage.setItem('theme', newValue ? 'dark' : 'light');
  const html = document.documentElement;
  html.classList.remove('dark', 'light');
  html.classList.add(newValue ? 'dark' : 'light');
}, { immediate: true });

// 主题覆盖配置可以保留在 App.vue，因为它是全局的
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
.fullscreen-container { display: flex; justify-content: center; align-items: center; height: 100vh; width: 100%; background-color: #f0f2f5; }
.dark-mode .fullscreen-container { background-color: #101014; }
</style>
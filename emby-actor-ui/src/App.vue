<!-- src/App.vue -->
<template>
  <n-config-provider :theme="isDarkTheme ? darkTheme : undefined" :theme-overrides="currentNaiveTheme" :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider>
      <n-dialog-provider>
        <AppContent />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue';
import { NConfigProvider, NMessageProvider, NDialogProvider, darkTheme, zhCN, dateZhCN } from 'naive-ui';
import AppContent from './AppContent.vue';

const isDarkTheme = ref(localStorage.getItem('isDark') === 'true');
const currentNaiveTheme = ref({});

onMounted(() => {
    const app = document.getElementById('app');
    
    app.addEventListener('update-naive-theme', (event) => {
        currentNaiveTheme.value = event.detail;
    });

    app.addEventListener('update-dark-mode', (event) => {
        isDarkTheme.value = event.detail;
    });
});
</script>

<style>
html, body { height: 100vh; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; overflow: hidden; }
.fullscreen-container { display: flex; justify-content: center; align-items: center; height: 100vh; width: 100%; }
html.light .fullscreen-container { background-color: #f0f2f5; }
html.dark .fullscreen-container { background-color: #101014; }
</style>
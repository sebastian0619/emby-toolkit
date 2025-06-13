// src/stores/auth.js

import { defineStore } from 'pinia';
import { ref } from 'vue';
import axios from 'axios';

export const useAuthStore = defineStore('auth', () => {
  // --- State ---
  const isLoggedIn = ref(false);
  const isAuthEnabled = ref(true); // 默认假设认证是开启的
  const username = ref(null);
  const initializationError = ref(null);

  // --- Actions ---
  async function checkAuthStatus() {
    try {
      const response = await axios.get('/api/auth/status');
      isAuthEnabled.value = response.data.auth_enabled;
      isLoggedIn.value = response.data.logged_in;
      username.value = response.data.username;
      initializationError.value = null;
      return response.data;
    } catch (error) {
      console.error('检查认证状态失败:', error);
      initializationError.value = '无法连接到后端服务，请检查服务是否运行。';
      // 发生错误时，重置为未登录状态
      isAuthEnabled.value = true; // 无法确定时，保守起见，要求登录
      isLoggedIn.value = false;
      username.value = null;
      throw error; // 抛出错误让调用者知道
    }
  }

  async function login(credentials) {
    const response = await axios.post('/api/auth/login', credentials);
    // 登录成功后，立即更新状态
    if (response.status === 200) {
      await checkAuthStatus();
    }
    return response;
  }

  async function logout() {
    await axios.post('/api/auth/logout');
    // 登出后，也立即更新状态
    await checkAuthStatus();
  }

  return {
    isLoggedIn,
    isAuthEnabled,
    username,
    initializationError,
    checkAuthStatus,
    login,
    logout,
  };
});
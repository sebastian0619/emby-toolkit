// src/stores/auth.js

import { defineStore } from 'pinia';
import { ref } from 'vue';
import axios from 'axios';

export const useAuthStore = defineStore('auth', () => {
  // --- State ---
  const isLoggedIn = ref(false);
  const isAuthEnabled = ref(true);
  const username = ref(null);
  const initializationError = ref(null);
  // 【【【新增1】】】: 在 state 中定义强制修改密码的标志
  const forceChangePassword = ref(false);

  // --- Actions ---
  async function checkAuthStatus() {
    try {
      const response = await axios.get('/api/auth/status');
      isAuthEnabled.value = response.data.auth_enabled;
      isLoggedIn.value = response.data.logged_in;
      username.value = response.data.username;
      initializationError.value = null;
      
      // 如果检查后发现未登录，确保重置强制修改密码的标志
      if (!isLoggedIn.value) {
        forceChangePassword.value = false;
      }

      return response.data;
    } catch (error) {
      console.error('检查认证状态失败:', error);
      initializationError.value = '无法连接到后端服务，请检查服务是否运行。';
      isAuthEnabled.value = true;
      isLoggedIn.value = false;
      username.value = null;
      forceChangePassword.value = false; // 出错时也要重置
      throw error;
    }
  }

  // 【【【修改】】】: 重写 login 函数，使其直接处理登录响应
  async function login(credentials) {
    // 发送登录请求
    const response = await axios.post('/api/auth/login', credentials);
    
    // 直接使用 /login 接口的返回数据来更新状态
    isLoggedIn.value = true;
    username.value = response.data.username;
    forceChangePassword.value = response.data.force_change_password; // <--- 保存关键标志

    return response; // 将原始响应返回，以防万一
  }

  // 【【【修改】】】: 重写 logout 函数，使其更直接
  async function logout() {
    try {
        await axios.post('/api/auth/logout');
    } catch (error) {
        console.error("登出时后端发生错误:", error);
    } finally {
        // 无论后端是否成功，前端都必须清理状态
        isLoggedIn.value = false;
        username.value = null;
        forceChangePassword.value = false; // <--- 重置标志
    }
  }

  // --- Return ---
  // 【【【新增2】】】: 确保将新状态和修改后的 action 导出
  return {
    isLoggedIn,
    isAuthEnabled,
    username,
    initializationError,
    forceChangePassword, // <--- 导出新状态
    checkAuthStatus,
    login,
    logout,
  };
});
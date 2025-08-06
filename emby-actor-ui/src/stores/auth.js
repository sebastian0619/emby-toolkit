// src/stores/auth.js (最终正确版)

import { defineStore } from 'pinia';
import { ref } from 'vue';
import axios from 'axios';

export const useAuthStore = defineStore('auth', () => {
  // --- State ---
  const isLoggedIn = ref(false);
  const isAuthEnabled = ref(true);
  const username = ref(null);
  const initializationError = ref(null);
  const forceChangePassword = ref(false);

  // --- Actions ---
  async function checkAuthStatus() {
    try {
      const response = await axios.get('/api/auth/status');
      isAuthEnabled.value = response.data.auth_enabled;
      isLoggedIn.value = response.data.logged_in;
      username.value = response.data.username;
      initializationError.value = null;
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
      forceChangePassword.value = false;
      throw error;
    }
  }

  // ★★★ 核心修改在这里 ★★★
  async function login(credentials) {
    try {
      const response = await axios.post('/api/auth/login', credentials);
      
      // 1. 检查后端返回的数据是否表示成功。
      //    我们假设成功的响应总是包含一个 username 字段。
      //    如果您的后端成功时不一定返回 username，可以换成检查 response.status === 200
      if (response.data && response.data.username) {
        // 2. 只有在业务逻辑真正成功时，才更新前端状态
        isLoggedIn.value = true;
        username.value = response.data.username;
        forceChangePassword.value = response.data.force_change_password;
        // 成功时，函数正常结束
      } else {
        // 3. 如果后端返回了 200 OK 但内容表示失败，我们主动抛出一个错误
        throw new Error('后端返回了意外的成功响应结构。');
      }
    } catch (error) {
      // 4. 捕获所有错误（axios抛出的网络/HTTP错误，或我们自己抛出的业务错误）
      console.error("登录时发生错误:", error);
      // 5. 将错误再次向上抛出，让调用它的组件（Login.vue）去处理
      throw error;
    }
  }

  async function logout() {
    try {
        await axios.post('/api/auth/logout');
    } catch (error) {
        console.error("登出时后端发生错误:", error);
    } finally {
        isLoggedIn.value = false;
        username.value = null;
        forceChangePassword.value = false;
    }
  }

  // --- Return ---
  return {
    isLoggedIn,
    isAuthEnabled,
    username,
    initializationError,
    forceChangePassword,
    checkAuthStatus,
    login,
    logout,
  };
});
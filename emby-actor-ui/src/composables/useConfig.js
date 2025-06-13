// src/composables/useConfig.js (最终全自动修复版)

import { ref } from 'vue';
import axios from 'axios';

// 1. 定义一个简单的布尔标志，在模块作用域内，确保只请求一次
let hasFetched = false;

// 2. 将所有状态变量移到外部，创建真正的单例
const configModel = ref(null); // ★★★ 初始值设为 null
const loadingConfig = ref(false); // 初始加载状态为 false
const savingConfig = ref(false);
const configError = ref(null);

export function useConfig() {
  const fetchConfigData = async () => {
    // 如果正在加载中，或者已经成功获取过了，就直接返回，避免重复请求
    if (loadingConfig.value || hasFetched) {
      return;
    }

    loadingConfig.value = true;
    configError.value = null;
    try {
      const response = await axios.get('/api/config');
      configModel.value = response.data;
      hasFetched = true; // ★★★ 请求成功后，将标志设为 true
    } catch (err) {
      console.error('useConfig: 获取配置失败:', err);
      configError.value = err.response?.data?.error || '无法连接到后端或解析配置。';
      hasFetched = false; // ★★★ 请求失败，允许下次重试
    } finally {
      loadingConfig.value = false;
    }
  };

  const handleSaveConfig = async () => {
    if (!configModel.value) {
      console.error('没有可保存的配置。');
      return false;
    }
    savingConfig.value = true;
    configError.value = null;
    try {
      await axios.post('/api/config', configModel.value);
      return true;
    } catch (err) {
      console.error('useConfig: 保存配置失败:', err);
      configError.value = err.response?.data?.error || err.message;
      return false;
    } finally {
      savingConfig.value = false;
    }
  };

  // ★★★ 核心自动加载逻辑 ★★★
  // 如果从未获取过数据，并且当前没有在加载中，就立即触发获取
  if (!hasFetched && !loadingConfig.value) {
    fetchConfigData();
  }

  return {
    configModel,
    loadingConfig,
    savingConfig,
    configError,
    fetchConfigData,
    handleSaveConfig,
  };
}
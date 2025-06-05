<template>
  <n-form :model="configModel" label-placement="left" label-width="auto" require-mark-placement="right-hanging" style="margin-top:15px;">
    <n-grid :cols="1" :x-gap="24">
      <n-form-item-grid-item label="Emby 服务器 URL" path="emby_server_url">
        <n-input v-model:value="configModel.emby_server_url" placeholder="例如: http://localhost:8096" />
      </n-form-item-grid-item>
      <n-form-item-grid-item label="Emby API Key" path="emby_api_key">
        <n-input v-model:value="configModel.emby_api_key" type="password" show-password-on="click" placeholder="输入你的 Emby API Key" />
      </n-form-item-grid-item>
      <n-form-item-grid-item label="Emby 用户 ID" path="emby_user_id">
        <n-input v-model:value="configModel.emby_user_id" placeholder="通常用于特定用户操作或获取库列表" />
      </n-form-item-grid-item>
      <n-form-item-grid-item label="更新后刷新 Emby 媒体项">
        <n-switch v-model:value="configModel.refresh_emby_after_update" />
      </n-form-item-grid-item>

      <n-form-item-grid-item path="libraries_to_process">
        <template #label>
          <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
            <span>选择要处理的媒体库</span>
            <n-button size="small" type="primary" @click="savePageConfig" :loading="savingConfig" style="margin-left: 10px;">
              保存 Emby 配置
            </n-button>
          </div>
        </template>
        <n-spin :show="loadingLibraries">
          <n-checkbox-group v-model:value="configModel.libraries_to_process">
            <n-space item-style="display: flex;">
              <n-checkbox v-for="lib in availableLibraries" :key="lib.Id" :value="lib.Id" :label="lib.Name" />
            </n-space>
          </n-checkbox-group>
          <n-text depth="3" v-if="!loadingLibraries && availableLibraries.length === 0 && (configModel.emby_server_url && configModel.emby_api_key)">
            未找到媒体库。请检查 Emby URL 和 API Key 配置，并确保 Emby 服务可访问。
          </n-text>
          <div v-if="libraryError" style="color: red; margin-top: 5px;">{{ libraryError }}</div>
        </n-spin>
      </n-form-item-grid-item>
    </n-grid>
  </n-form>
</template>

<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue';
import {
  NForm, NFormItemGridItem, NInput, NSwitch, NCheckboxGroup, NCheckbox,
  NSpace, NSpin, NText, NGrid, NButton,
  useMessage // 导入 useMessage
} from 'naive-ui';
import { useConfig } from '../../composables/useConfig.js';
import axios from 'axios';

const message = useMessage(); // 在组件 setup 中获取 message 实例

const {
  configModel,
  handleSaveConfig, // 这个是 useConfig 里的原始保存函数
  savingConfig,
  configError, // 获取错误状态
  loadingConfig: globalLoadingConfig
} = useConfig();

const availableLibraries = ref([]);
const loadingLibraries = ref(false);
const libraryError = ref(null);
const componentIsMounted = ref(false);
let unwatchGlobalConfig = null;
let unwatchEmbyConfig = null;

const fetchEmbyLibrariesInternal = async (reason = "unknown") => {
  if (!configModel.value.emby_server_url || !configModel.value.emby_api_key) {
    availableLibraries.value = [];
    return;
  }
  if (loadingLibraries.value) return;
  loadingLibraries.value = true;
  libraryError.value = null;
  try {
    const response = await axios.get(`/api/emby_libraries`);
    if (response.data && Array.isArray(response.data)) {
      availableLibraries.value = response.data;
      if (response.data.length === 0) libraryError.value = "从 Emby 获取到的媒体库列表为空。";
    } else {
      availableLibraries.value = [];
      libraryError.value = "获取媒体库列表格式不正确或后端返回为空。";
    }
  } catch (err) {
    availableLibraries.value = [];
    libraryError.value = `获取 Emby 媒体库失败: ${err.response?.data?.error || err.message}`;
  } finally {
    loadingLibraries.value = false;
  }
};

onMounted(() => {
  componentIsMounted.value = true;
  const initFetch = async () => {
    if (configModel.value.emby_server_url && configModel.value.emby_api_key) {
      await fetchEmbyLibrariesInternal("initial mount, global config ready");
    }
  };

  if (globalLoadingConfig.value) {
    unwatchGlobalConfig = watch(globalLoadingConfig, async (isLoading) => {
      if (!isLoading && componentIsMounted.value) {
        await initFetch();
        if (unwatchGlobalConfig) unwatchGlobalConfig();
      }
    });
  } else {
    initFetch();
  }

  unwatchEmbyConfig = watch(
    () => [configModel.value.emby_server_url, configModel.value.emby_api_key],
    async ([newUrl, newKey], [oldUrl, oldKey]) => {
      if (!componentIsMounted.value) return;
      if (newUrl !== oldUrl || newKey !== oldKey) {
        await fetchEmbyLibrariesInternal("url/key changed in watch");
      }
    },
    { immediate: false }
  );
});

onUnmounted(() => {
  componentIsMounted.value = false;
  if (unwatchGlobalConfig) unwatchGlobalConfig();
  if (unwatchEmbyConfig) unwatchEmbyConfig();
});

// 新建一个函数来处理保存和消息提示
const savePageConfig = async () => {
  const success = await handleSaveConfig();
  if (success) {
    message.success('Emby 配置已成功保存！');
    // 成功保存后，可能需要重新获取媒体库，因为URL或Key可能已更改
    if (configModel.value.emby_server_url && configModel.value.emby_api_key) {
        await fetchEmbyLibrariesInternal("after config save");
    } else {
        availableLibraries.value = []; // 如果URL或Key被清空，则清空库列表
    }
  } else {
    message.error(configError.value || 'Emby 配置保存失败，请检查日志。');
  }
};
</script>
<template>
  <n-layout content-style="padding: 24px;">
  <div v-if="configModel">
  <n-space vertical :size="24" style="margin-top: 15px;">
    <!-- Emby 连接设置卡片 (保持不变) -->
    <n-card title="Emby 连接设置" class="glass-section" :bordered="false">
      <n-form :model="configModel" label-placement="left" label-width="auto" require-mark-placement="right-hanging">
        <n-grid :cols="1" :x-gap="24">
          <n-form-item-grid-item label="Emby 服务器 URL" path="emby_server_url">
            <n-input v-model:value="configModel.emby_server_url" placeholder="例如: http://localhost:8096" />
          </n-form-item-grid-item>
          <n-form-item-grid-item label="Emby API Key" path="emby_api_key">
            <n-input v-model:value="configModel.emby_api_key" type="password" show-password-on="click" placeholder="输入你的 Emby API Key" />
          </n-form-item-grid-item>
          <n-form-item-grid-item label="Emby 用户 ID" :rule="embyUserIdRule" path="emby_user_id">
          <n-input v-model:value="configModel.emby_user_id" placeholder="请输入32位的用户ID，而非用户名" />
          <!-- 在输入框下方增加一个友好的提示 -->
          <template #feedback>
            <!-- 当输入格式错误时，显示红色警告 -->
            <div v-if="isInvalidUserId" style="color: #e88080; margin-top: 4px; font-size: 12px;">
              格式错误！ID通常是一串32位的字母和数字，是Emby后台用户管理的地址栏userId=后面那串由32个字母和数字组成的长ID。
            </div>
            <!-- 默认情况下，显示灰色提示 -->
            <div v-else style="font-size: 12px; color: #888; margin-top: 4px;">
              提示：这不是用户名。请前往 Emby 后台 -> 用户管理 -> 点击你的账户 -> 然后从浏览器地址栏中复制userId=后面那串由32个字母和数字组成的长ID。
            </div>
          </template>
        </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>


    <!-- 媒体库选择卡片 (保持不变) -->
    <n-card class="glass-section" :bordered="false">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
          <span>选择要处理的媒体库</span>
          <n-button size="small" type="primary" @click="savePageConfig" :loading="savingConfig">
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
    </n-card>
  </n-space>
  </div>
  </n-layout>
</template>

<script setup>
// ... 你的 <script setup> 部分完全不需要任何修改 ...
import { ref, watch, onMounted, onUnmounted, computed } from 'vue';
import {
  NForm, NFormItemGridItem, NInput, NSwitch, NCheckboxGroup, NCheckbox,
  NSpace, NSpin, NText, NGrid, NButton, NCard, // ★★★ 确保导入了 NCard ★★★
  useMessage
} from 'naive-ui';
import { useConfig } from '../../composables/useConfig.js';
import axios from 'axios';

const message = useMessage();
// ★★★ START: 新增的用户ID校验逻辑 ★★★

// 正则表达式，用于匹配32位的十六进制字符串
const embyUserIdRegex = /^[a-f0-9]{32}$/i;

// 计算属性，用于判断当前输入是否是无效格式
const isInvalidUserId = computed(() => {
  // 确保 configModel.value 存在后再访问
  if (!configModel.value || !configModel.value.emby_user_id) {
    return false;
  }
  const userId = configModel.value.emby_user_id.trim();
  // 只有当用户输入了内容，但格式又不匹配时，才认为是“无效”的
  return userId !== '' && !embyUserIdRegex.test(userId);
});

// 表单验证规则，在点击保存时会触发
const embyUserIdRule = {
  trigger: ['input', 'blur'],
  validator(rule, value) {
    if (value && !embyUserIdRegex.test(value)) {
      // 这个错误信息会显示在 label 旁边
      return new Error('ID格式不正确，应为32位字母和数字组合。');
    }
    return true;
  }
};

// ★★★ END: 新增的用户ID校验逻辑 ★★★

const {
  configModel,
  handleSaveConfig,
  savingConfig,
  configError,
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
    // 1. 直接监听 configModel 这个 ref 对象
    configModel, 
    // 2. 在回调函数中，先检查 newValue 是否有值
    async (newValue, oldValue) => {
      if (!componentIsMounted.value || !newValue) {
        // 如果组件已卸载，或者新的 configModel 还是 null，就什么都不做
        return;
      }
      
      // 只有在 newValue 存在时，才安全地访问它的属性
      const newUrl = newValue.emby_server_url;
      const newKey = newValue.emby_api_key;
      const oldUrl = oldValue?.emby_server_url; // 使用可选链 ?. 来安全地访问旧值
      const oldKey = oldValue?.emby_api_key;

      if (newUrl !== oldUrl || newKey !== oldKey) {
        await fetchEmbyLibrariesInternal("url/key changed in watch");
      }
    },
    { 
      deep: true // ★★★ 使用 deep: true 来监听对象内部属性的变化 ★★★
    }
  );
});

onUnmounted(() => {
  componentIsMounted.value = false;
  if (unwatchGlobalConfig) unwatchGlobalConfig();
  if (unwatchEmbyConfig) unwatchEmbyConfig();
});

const savePageConfig = async () => {
  const success = await handleSaveConfig();

  if (success) {
    message.success('Emby 配置已成功保存！');
    if (configModel.value.emby_server_url && configModel.value.emby_api_key) {
        await fetchEmbyLibrariesInternal("after config save");
    } else {
        availableLibraries.value = [];
    }
  } else {
    message.error(configError.value || 'Emby 配置保存失败，请检查日志。');
  }
};
</script>
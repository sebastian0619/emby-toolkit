<template>
  <n-form :model="configModel" label-placement="top" style="margin-top:15px;">
    <n-grid :cols="1" :x-gap="24">
      
      <!-- ✨✨✨ 1. 把所有通用数值设置放在一起 ✨✨✨ -->
      <n-form-item-grid-item label="处理项目间的延迟 (秒)" path="delay_between_items_sec">
        <n-input-number v-model:value="configModel.delay_between_items_sec" :min="0" :step="0.1" placeholder="例如: 0.5" />
      </n-form-item-grid-item>
      
      <n-form-item-grid-item label="豆瓣API默认冷却时间 (秒)" path="api_douban_default_cooldown_seconds">
        <n-input-number v-model:value="configModel.api_douban_default_cooldown_seconds" :min="0.1" :step="0.1" placeholder="例如: 1.0" />
      </n-form-item-grid-item>

      <!-- ✨✨✨ 2. 把评分阈值移动到这里 ✨✨✨ -->
      <n-form-item-grid-item label="需手动处理的最低评分阈值" path="min_score_for_review">
        <n-input-number v-model:value="configModel.min_score_for_review" :min="0" :max="10" :step="0.1" placeholder="例如: 6.0" />
        <template #feedback>
          <n-text depth="3" style="font-size:0.8em;">处理质量评分低于此值的项目将进入待复核列表。</n-text>
        </template>
      </n-form-item-grid-item>

      <!-- AI 翻译设置区域 -->
      <n-divider title-placement="left">AI 翻译设置</n-divider>

      <n-form-item-grid-item label="启用 AI 翻译">
        <n-switch v-model:value="configModel.ai_translation_enabled" />
        <template #feedback>
          开启后，将优先使用下方配置的AI服务进行翻译，否则将使用传统的翻译引擎。
        </template>
      </n-form-item-grid-item>

      <n-form-item-grid-item label="AI 服务商" path="ai_provider">
        <n-select
          v-model:value="configModel.ai_provider"
          :options="aiProviderOptions"
          placeholder="选择一个AI服务商"
        />
      </n-form-item-grid-item>

      <n-form-item-grid-item label="API Key" path="ai_api_key">
        <n-input v-model:value="configModel.ai_api_key" type="password" show-password-on="click" placeholder="输入你的AI服务商提供的API Key" />
      </n-form-item-grid-item>
      
      <n-form-item-grid-item label="模型名称" path="ai_model_name">
        <n-input v-model:value="configModel.ai_model_name" placeholder="例如: gpt-3.5-turbo, glm-4" />
      </n-form-item-grid-item>

      <n-form-item-grid-item label="API Base URL (可选)" path="ai_base_url">
        <n-input v-model:value="configModel.ai_base_url" placeholder="用于代理或第三方兼容服务，例如: https://api.siliconflow.cn/v1" />
      </n-form-item-grid-item>

      <n-form-item-grid-item label="翻译提示词 (Prompt)" path="ai_translation_prompt">
        <n-input
          v-model:value="configModel.ai_translation_prompt"
          type="textarea"
          :autosize="{ minRows: 5, maxRows: 15 }"
          placeholder="输入指导AI如何进行翻译的系统提示词"
        />
      </n-form-item-grid-item>

      <!-- ✨✨✨ 3. 保存按钮现在在所有设置的末尾，位置不变，但逻辑上更合理 ✨✨✨ -->
      <n-form-item-grid-item>
        <n-button size="medium" type="primary" @click="savePageConfig" :loading="savingConfig" block>
          保存通用设置
        </n-button>
      </n-form-item-grid-item>

    </n-grid>
  </n-form>
</template>

<script setup>
import {
  // ✨✨✨ 确保导入了这些新组件 ✨✨✨
  NForm, NFormItemGridItem, NInputNumber, NGrid, NText,
  NButton, NDivider, NSwitch, NSelect, NInput,
  useMessage
} from 'naive-ui';
import { useConfig } from '../../composables/useConfig.js';
import { ref } from 'vue';

const message = useMessage(); // 获取实例

const {
    configModel,
    handleSaveConfig,
    savingConfig,
    configError
} = useConfig();

const aiProviderOptions = ref([
  {
    label: 'OpenAI (及兼容服务，如硅基流动)',
    value: 'openai'
  },
  {
    label: '智谱AI (ZhipuAI)',
    value: 'zhipuai'
  }
  // 未来想支持更多，就在这里加
]);

const savePageConfig = async () => {
  const success = await handleSaveConfig();
  if (success) {
    message.success('通用设置已成功保存！');
  } else {
    message.error(configError.value || '通用设置保存失败。');
  }
};
</script>
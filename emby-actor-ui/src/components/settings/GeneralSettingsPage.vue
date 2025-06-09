<template>
  <!-- ★★★ 1. 使用 n-space 作为根容器 ★★★ -->
  <n-space vertical :size="24" style="margin-top: 15px;">
    <!-- ★★★ 2. 第一个卡片，包裹常规参数 ★★★ -->
    <n-card title="常规参数" class="beautified-card" :bordered="false">
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1" :y-gap="18">
          <n-form-item-grid-item label="处理项目间的延迟 (秒)" path="delay_between_items_sec">
            <n-input-number v-model:value="configModel.delay_between_items_sec" :min="0" :step="0.1" placeholder="例如: 0.5" style="width: 100%;" />
          </n-form-item-grid-item>
          
          <n-form-item-grid-item label="豆瓣API默认冷却时间 (秒)" path="api_douban_default_cooldown_seconds">
            <n-input-number v-model:value="configModel.api_douban_default_cooldown_seconds" :min="0.1" :step="0.1" placeholder="例如: 1.0" style="width: 100%;" />
          </n-form-item-grid-item>

          <n-form-item-grid-item label="需手动处理的最低评分阈值" path="min_score_for_review">
            <n-input-number v-model:value="configModel.min_score_for_review" :min="0" :max="10" :step="0.1" placeholder="例如: 6.0" style="width: 100%;" />
            <template #feedback>
              <n-text depth="3" style="font-size:0.8em;">处理质量评分低于此值的项目将进入待复核列表。</n-text>
            </template>
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>

    <!-- ★★★ 3. 第二个卡片，包裹 AI 翻译设置 ★★★ -->
    <n-card title="AI 翻译设置" class="beautified-card" :bordered="false">
      <template #header-extra>
        <n-switch v-model:value="configModel.ai_translation_enabled">
          <template #checked>已启用</template>
          <template #unchecked>已禁用</template>
        </n-switch>
      </template>
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1" :y-gap="18">
          <n-form-item-grid-item label="AI 服务商" path="ai_provider">
            <n-select
              v-model:value="configModel.ai_provider"
              :options="aiProviderOptions"
              placeholder="选择一个AI服务商"
              :disabled="!configModel.ai_translation_enabled"
            />
          </n-form-item-grid-item>

          <n-form-item-grid-item label="API Key" path="ai_api_key">
            <n-input v-model:value="configModel.ai_api_key" type="password" show-password-on="click" placeholder="输入你的AI服务商提供的API Key" :disabled="!configModel.ai_translation_enabled" />
          </n-form-item-grid-item>
          
          <n-form-item-grid-item label="模型名称" path="ai_model_name">
            <n-input v-model:value="configModel.ai_model_name" placeholder="例如: gpt-3.5-turbo, glm-4" :disabled="!configModel.ai_translation_enabled" />
          </n-form-item-grid-item>

          <n-form-item-grid-item label="API Base URL (可选)" path="ai_base_url">
            <n-input v-model:value="configModel.ai_base_url" placeholder="用于代理或第三方兼容服务" :disabled="!configModel.ai_translation_enabled" />
          </n-form-item-grid-item>

          <n-form-item-grid-item label="翻译提示词 (Prompt)" path="ai_translation_prompt">
            <n-input
              v-model:value="configModel.ai_translation_prompt"
              type="textarea"
              :autosize="{ minRows: 5, maxRows: 15 }"
              placeholder="输入指导AI如何进行翻译的系统提示词"
              :disabled="!configModel.ai_translation_enabled"
            />
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>

    <!-- ★★★ 4. 页面底部的保存按钮 ★★★ -->
    <n-button size="medium" type="primary" @click="savePageConfig" :loading="savingConfig" block>
      保存通用设置
    </n-button>
  </n-space>
</template>

<script setup>
// ... 你的 <script setup> 部分完全不需要任何修改 ...
// ★★★ 只需要确保从 naive-ui 导入了 NCard, NSpace, NInputNumber ★★★
import {
  NForm, NFormItemGridItem, NInputNumber, NGrid, NText,
  NButton, NSwitch, NSelect, NInput, NCard, NSpace,
  useMessage
} from 'naive-ui';
import { useConfig } from '../../composables/useConfig.js';
import { ref } from 'vue';

const message = useMessage();

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
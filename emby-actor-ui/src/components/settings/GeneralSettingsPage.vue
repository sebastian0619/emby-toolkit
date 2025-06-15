<!-- src/components/settings/GeneralSettingsPage.vue -->
<template>
  <div v-if="configModel">
  <n-form
    v-if="configModel"
    @submit.prevent="save"
    label-placement="left"
    label-width="auto"
    label-align="right"
    :model="configModel"
  >
    <n-grid cols="1 m:2" :x-gap="24" :y-gap="24" responsive="screen">
      <!-- ########## 左侧列 ########## -->
      <n-gi>
        <n-space vertical :size="24">
          <!-- 卡片: 基础设置 -->
          <n-card title="基础设置" size="small" class="beautified-card">
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
            <n-form-item label="处理分集" path="process_episodes">
              <n-switch v-model:value="configModel.process_episodes" />
              <template #feedback>开启后，处理电视剧时会为每一季/每一集生成单独的元数据文件。</template>
            </n-form-item>
            <n-form-item label="同步图片" path="sync_images">
              <n-switch v-model:value="configModel.sync_images" />
              <template #feedback>开启后，处理媒体时会下载海报、横幅图等图片文件。</template>
            </n-form-item>
          </n-card>

          <!-- 卡片: 数据源与 API -->
          <n-card title="数据源与 API" size="small" class="beautified-card">
            <n-form-item label="本地数据源路径" path="local_data_path">
              <n-input v-model:value="configModel.local_data_path" placeholder="神医TMDB缓存目录 (cache和override的上层)" />
            </n-form-item>
            <n-form-item label="TMDB API Key" path="tmdb_api_key">
              <n-input type="password" show-password-on="mousedown" v-model:value="configModel.tmdb_api_key" placeholder="输入你的 TMDB API Key" />
            </n-form-item>
          </n-card>

          <!-- 卡片: 安全设置 -->
          <n-card v-if="authStore.isAuthEnabled" title="安全设置" size="small" class="beautified-card">
            <ChangePassword />
          </n-card>
        </n-space>
      </n-gi>

      <!-- ########## 右侧列 ########## -->
      <n-gi>
        <n-space vertical :size="24">
          <!-- 卡片: 传统翻译引擎 -->
          <n-card title="传统翻译引擎" size="small" class="beautified-card">
            <n-form-item label="翻译引擎顺序" path="translator_engines_order">
              <template #feedback>可拖动调整顺序，点击添加新的翻译引擎。</template>
              <draggable
                v-model="configModel.translator_engines_order"
                item-key="value"
                tag="div"
                class="engine-list"
                handle=".drag-handle"
                animation="300"
              >
                <template #item="{ element: engineValue, index }">
                  <n-tag :key="engineValue" type="primary" closable class="engine-tag" @close="removeEngine(index)">
                    <n-icon :component="DragHandleIcon" class="drag-handle" />
                    {{ getEngineLabel(engineValue) }}
                  </n-tag>
                </template>
              </draggable>
              <n-select
                v-if="unselectedEngines.length > 0"
                placeholder="点击添加新的翻译引擎..."
                :options="unselectedEngines"
                @update:value="addEngine"
                style="margin-top: 12px;"
              />
            </n-form-item>
          </n-card>

          <!-- 卡片: AI 翻译设置 -->
          <n-card title="AI 翻译设置" size="small" class="beautified-card">
            <template #header-extra>
              <n-switch v-model:value="configModel.ai_translation_enabled" />
            </template>
            <!-- ✨✨✨ 核心修改区域 START ✨✨✨ -->
            <div class="ai-settings-wrapper" :class="{ 'content-disabled': !configModel.ai_translation_enabled }">
              <n-form-item label="AI 服务商" path="ai_provider">
                <n-select 
                  v-model:value="configModel.ai_provider" 
                  :options="aiProviderOptions" 
                  :disabled="!configModel.ai_translation_enabled"
                />
              </n-form-item>
              <n-form-item label="API Key" path="ai_api_key">
                <n-input 
                  type="password" 
                  show-password-on="mousedown" 
                  v-model:value="configModel.ai_api_key" 
                  placeholder="输入 AI 服务的 API Key" 
                  :disabled="!configModel.ai_translation_enabled"
                />
              </n-form-item>
              <n-form-item label="模型名称" path="ai_model_name">
                <n-input 
                  v-model:value="configModel.ai_model_name" 
                  placeholder="例如: gpt-3.5-turbo, glm-4" 
                  :disabled="!configModel.ai_translation_enabled"
                />
              </n-form-item>
              <n-form-item label="API Base URL (可选)" path="ai_base_url">
                <n-input 
                  v-model:value="configModel.ai_base_url" 
                  placeholder="用于代理或第三方兼容服务" 
                  :disabled="!configModel.ai_translation_enabled"
                />
              </n-form-item>
              <n-form-item label="翻译提示词 (Prompt)" path="ai_translation_prompt">
                <n-input 
                  type="textarea" 
                  v-model:value="configModel.ai_translation_prompt" 
                  :autosize="{ minRows: 5, maxRows: 10 }" 
                  :disabled="!configModel.ai_translation_enabled"
                />
              </n-form-item>
            </div>
            <!-- ✨✨✨ 核心修改区域 END ✨✨✨ -->
          </n-card>
        </n-space>
      </n-gi>
    </n-grid>

    <!-- 页面底部的统一保存按钮 -->
    <n-button type="primary" attr-type="submit" :loading="savingConfig" block size="large" style="margin-top: 24px;">
      保存所有设置
    </n-button>
  </n-form>
  
  <n-alert v-else-if="configError" title="加载配置失败" type="error">
    {{ configError }}
  </n-alert>

  <div v-else>
    正在加载配置...
  </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import draggable from 'vuedraggable';
import { 
  NCard, NForm, NFormItem, NInputNumber, NSwitch, NButton, NGrid, NGi, 
  NSpin, NAlert, NInput, NSelect, NSpace, NTag, NIcon, useMessage,
  NFormItemGridItem
} from 'naive-ui';
import { MoveOutline as DragHandleIcon } from '@vicons/ionicons5';
import { useConfig } from '../../composables/useConfig.js';
import ChangePassword from './ChangePassword.vue';
import { useAuthStore } from '../../stores/auth';

const { configModel, loadingConfig, savingConfig, configError, handleSaveConfig } = useConfig();
const authStore = useAuthStore();
const message = useMessage();

// --- 保存逻辑 ---
async function save() {
  const success = await handleSaveConfig();
  if (success) {
    message.success('所有设置已成功保存！');
  } else {
    message.error(configError.value || '配置保存失败，请检查后端日志。');
  }
}
loadingConfig.value = false;
// --- 翻译引擎逻辑 ---
const availableTranslatorEngines = ref([
  { label: '必应 (Bing)', value: 'bing' },
  { label: '谷歌 (Google)', value: 'google' },
  { label: '百度 (Baidu)', value: 'baidu' },
  { label: '阿里 (Alibaba)', value: 'alibaba' },
  { label: '有道 (Youdao)', value: 'youdao' },
  { label: '腾讯 (Tencent)', value: 'tencent' },
]);

const getEngineLabel = (value) => {
  const engine = availableTranslatorEngines.value.find(e => e.value === value);
  return engine ? engine.label : value;
};

const unselectedEngines = computed(() => {
  if (!configModel.value?.translator_engines_order) return availableTranslatorEngines.value;
  const selectedValues = new Set(configModel.value.translator_engines_order);
  return availableTranslatorEngines.value.filter(engine => !selectedValues.has(engine.value));
});

const addEngine = (value) => {
  if (!configModel.value.translator_engines_order) {
    configModel.value.translator_engines_order = [];
  }
  if (value && !configModel.value.translator_engines_order.includes(value)) {
    configModel.value.translator_engines_order.push(value);
  }
};

const removeEngine = (index) => {
  configModel.value.translator_engines_order.splice(index, 1);
};

// --- AI 服务商逻辑 ---
const aiProviderOptions = ref([
  { label: 'OpenAI (及兼容服务)', value: 'openai' },
  { label: '智谱AI (ZhipuAI)', value: 'zhipuai' },
]);

</script>

<style scoped>
/* 禁用AI设置时的遮罩效果 */
.ai-settings-wrapper {
  transition: opacity 0.3s ease;
}
/* 
  当使用方案B时，我们不再需要 pointer-events: none，
  因为组件的 :disabled 属性已经处理了交互。
  我们只保留一个透明度变化来提供视觉反馈。
*/
.content-disabled {
  opacity: 0.6;
}

/* 翻译引擎标签样式 */
.engine-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.engine-tag {
  cursor: grab;
}
.engine-tag:active {
  cursor: grabbing;
}
.drag-handle {
  margin-right: 6px;
  vertical-align: -0.15em;
}
</style>
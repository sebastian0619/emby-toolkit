<template>
  <n-space vertical :size="24" style="margin-top: 15px;">
    <!-- ... 其他卡片保持不变 ... -->
    <n-card title="TMDB API 设置" class="beautified-card" :bordered="false">
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1">
          <n-form-item-grid-item label="TMDB API Key (v3)" path="tmdb_api_key">
            <n-input v-model:value="configModel.tmdb_api_key" placeholder="输入你的 TMDB API Key (v3 Auth)" />
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>

    <n-card title="翻译配置" class="beautified-card" :bordered="false">
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1">
          <n-form-item-grid-item label="翻译引擎顺序 (可拖动调整)" path="translator_engines_order">
            
            <draggable
              v-model="configModel.translator_engines_order"
              item-key="value"
              tag="div"
              class="engine-list"
              handle=".drag-handle"
              animation="300"
            >
              <template #item="{ element: engineValue, index }">
                <n-tag
                  :key="engineValue"
                  type="primary"
                  closable
                  class="engine-tag"
                  @close="removeEngine(index)"
                >
                  <n-icon :component="DragHandleIcon" class="drag-handle" />
                  <!-- 这里会自动显示中文名，因为 getEngineLabel 会读取新的 label -->
                  {{ getEngineLabel(engineValue) }}
                </n-tag>
              </template>
            </draggable>

            <n-select
              v-if="unselectedEngines.length > 0"
              placeholder="点击添加新的翻译引擎..."
              :options="unselectedEngines"
              @update:value="addEngine"
              style="margin-top: 10px;"
            />
            <n-text v-else depth="3" style="font-size:0.8em; margin-top: 10px; display: block;">
              所有可用引擎都已添加。
            </n-text>

          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>

    <n-card class="beautified-card" :bordered="false">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
          <span>数据源配置</span>
          <n-button size="small" type="primary" @click="savePageConfig" :loading="savingConfig">
            保存所有设置
          </n-button>
        </div>
      </template>
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1" :y-gap="18">
          <n-form-item-grid-item label="本地数据源路径 (神医TMDB缓存目录)" path="local_data_path">
              <n-input v-model:value="configModel.local_data_path" placeholder="cache和override的上层目录" />
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>
  </n-space>
</template>

<script setup>
import { ref, computed } from 'vue';
import draggable from 'vuedraggable';
import {
  NForm, NFormItemGridItem, NInput, NSelect, NGrid, NText,
  NButton, NCard, NSpace, NTag, NIcon,
  useMessage
} from 'naive-ui';
import { MoveOutline as DragHandleIcon } from '@vicons/ionicons5';
import { useConfig } from '../../composables/useConfig.js';

const message = useMessage();

const {
    configModel,
    handleSaveConfig,
    savingConfig,
    configError
} = useConfig();

// ★★★ START: 核心修改 - 将 label 改为中文 ★★★
const availableTranslatorEngines = ref([
  { label: '必应 (Bing)', value: 'bing' },
  { label: '谷歌 (Google)', value: 'google' },
  { label: '百度 (Baidu)', value: 'baidu' },
  { label: '阿里 (Alibaba)', value: 'alibaba' },
  { label: '有道 (Youdao)', value: 'youdao' },
  { label: '腾讯 (Tencent)', value: 'tencent' },
]);
// ★★★ END: 核心修改 ★★★


const getEngineLabel = (value) => {
  const engine = availableTranslatorEngines.value.find(e => e.value === value);
  return engine ? engine.label : value;
};

const unselectedEngines = computed(() => {
  const selectedValues = new Set(configModel.value.translator_engines_order || []);
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

const savePageConfig = async () => {
  const success = await handleSaveConfig();
  if (success) {
    message.success('所有配置已成功保存！');
  } else {
    message.error(configError.value || '配置保存失败。');
  }
};
</script>

<style scoped>
.engine-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 8px;
  border: 1px solid var(--n-border-color);
  border-radius: var(--n-border-radius);
  background-color: var(--n-action-color);
}

.engine-tag {
  display: flex;
  align-items: center;
  padding: 0 10px;
  height: 34px;
  background-color: var(--n-color);
  border: 1px solid var(--n-border-color);
}

.drag-handle {
  cursor: grab;
  margin-right: 8px;
  color: var(--n-text-color-3);
}
.drag-handle:active {
  cursor: grabbing;
}

.sortable-ghost {
  opacity: 0.4;
  background: var(--n-color-target);
}
.sortable-drag {
  opacity: 1 !important;
  box-shadow: var(--n-box-shadow-2);
}
</style>
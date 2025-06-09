<template>
  <n-space vertical :size="24" style="margin-top: 15px;">
    <!-- ... 第一个卡片保持不变 ... -->
    <n-card title="TMDB API 设置" class="beautified-card" :bordered="false">
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1">
          <n-form-item-grid-item label="TMDB API Key (v3)" path="tmdb_api_key">
            <n-input v-model:value="configModel.tmdb_api_key" placeholder="输入你的 TMDB API Key (v3 Auth)" />
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>

    <!-- ★★★ START: 核心修改 - 升级翻译配置为可拖拽、可选择的标签选择器 ★★★ -->
    <n-card title="翻译配置" class="beautified-card" :bordered="false">
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1">
          <n-form-item-grid-item label="翻译引擎顺序 (可拖动调整)" path="translator_engines_order">
            <n-select
              v-model:value="configModel.translator_engines_order"
              multiple
              tag
              filterable
              placeholder="从列表选择或直接输入引擎名"
              :options="availableTranslatorEngines"
            />
            <template #feedback>
              <n-text depth="3" style="font-size:0.8em;">
                第一个引擎为首选。支持的引擎: bing, google, baidu, alibaba, youdao, tencent.
              </n-text>
            </template>
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>
    <!-- ★★★ END: 核心修改 ★★★ -->

    <!-- ... 第三个卡片保持不变 ... -->
    <n-card class="beautified-card" :bordered="false">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
          <span>数据源配置</span>
          <n-button size="small" type="primary" @click="savePageConfig" :loading="savingConfig">
            保存数据源配置
          </n-button>
        </div>
      </template>
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1" :y-gap="18">
          <n-form-item-grid-item label="本地数据源路径 (神医本地豆瓣缓存目录)" path="local_data_path">
              <n-input v-model:value="configModel.local_data_path" placeholder="例如: /path/to/your/actor_data" />
          </n-form-item-grid-item>

          <n-form-item-grid-item label="豆瓣数据源处理策略" path="data_source_mode">
            <n-select v-model:value="configModel.data_source_mode" :options="domesticSourceOptions" />
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>
  </n-space>
</template>

<script setup>
// ... 你的 <script setup> 部分完全不需要任何修改 ...
// ★★★ 只需要确保从 naive-ui 导入了 NCard 和 NSpace ★★★
import { ref } from 'vue';
import {
  NForm, NFormItemGridItem, NInput, NSelect, NGrid, NText,
  NButton, NCard, NSpace,
  useMessage
} from 'naive-ui';
import { useConfig } from '../../composables/useConfig.js';

const message = useMessage();

const {
    configModel,
    handleSaveConfig,
    savingConfig,
    configError
} = useConfig();

const availableTranslatorEngines = ref([
  { label: 'Bing', value: 'bing' },
  { label: 'Google', value: 'google' },
  { label: 'Baidu', value: 'baidu' },
  { label: 'Alibaba', value: 'alibaba' },
  { label: 'Youdao', value: 'youdao' },
  { label: 'Tencent', value: 'tencent' },
]);

const domesticSourceOptions = ref([
  { label: '豆瓣本地优先，在线备选 (推荐)', value: 'local_then_online' },
  { label: '仅在线豆瓣API', value: 'online_only' },
  { label: '仅豆瓣本地数据 (神医刮削)', value: 'local_only' },
  { label: '禁用豆瓣数据源', value: 'disabled_douban' }
]);

const savePageConfig = async () => {
  const success = await handleSaveConfig();
  if (success) {
    message.success('API 与数据源配置已成功保存！');
  } else {
    message.error(configError.value || 'API 与数据源配置保存失败。');
  }
};
</script>
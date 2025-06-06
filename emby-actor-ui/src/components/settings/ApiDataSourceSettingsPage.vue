<template>
  <n-form :model="configModel" label-placement="top" style="margin-top:15px;">
    <n-grid :cols="1" :x-gap="24">
      <n-form-item-grid-item label="TMDB API Key (v3)" path="tmdb_api_key">
        <n-input v-model:value="configModel.tmdb_api_key" placeholder="输入你的 TMDB API Key (v3 Auth)" />
      </n-form-item-grid-item>

      <n-divider title-placement="left">翻译配置</n-divider>
      <n-form-item-grid-item label="翻译引擎顺序 (英文逗号隔开)" path="translator_engines_order_str">
        <n-input v-model:value="configModel.translator_engines_order_str" placeholder="例如: bing,google,baidu" />
         <n-text depth="3" style="font-size:0.8em; margin-top:3px;">支持的引擎: bing, google, baidu, alibaba, youdao, tencent (顺序优先)</n-text>
      </n-form-item-grid-item>

      <n-divider title-placement="left">数据源配置</n-divider>
      <n-form-item-grid-item label="本地数据源路径 (神医本地豆瓣缓存目录)" path="local_data_path">
          <n-input v-model:value="configModel.local_data_path" placeholder="例如: /path/to/your/actor_data" />
      </n-form-item-grid-item>

      <n-form-item-grid-item path="data_source_mode">
        <template #label>
          <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
            <span>豆瓣数据源处理策略</span>
            <n-button size="small" type="primary" @click="savePageConfig" :loading="savingConfig" style="margin-left: 10px;">
              保存 API 与数据源配置
            </n-button>
          </div>
        </template>
        <n-select v-model:value="configModel.data_source_mode" :options="domesticSourceOptions" />
      </n-form-item-grid-item>
    </n-grid>
  </n-form>
</template>

<script setup>
import { ref } from 'vue';
import {
  NForm, NFormItemGridItem, NInput, NSelect, NDivider, NGrid, NText,
  NButton,
  useMessage // 导入 useMessage
} from 'naive-ui';
import { useConfig } from '../../composables/useConfig.js';

const message = useMessage(); // 获取实例

const {
    configModel,
    handleSaveConfig,
    savingConfig,
    configError
} = useConfig();

const domesticSourceOptions = ref([
  { label: '豆瓣本地优先，在线备选 (推荐)', value: 'local_then_online' },
  { label: '仅在线豆瓣API', value: 'online_only' },
  { label: '仅豆瓣本地数据 (神医刮削)', value: 'local_only' },
  { label: '禁用豆瓣数据源', value: 'disabled_douban' }
]);

const savePageConfig = async () => {
  console.log('[ApiDataSourcePage] BEFORE save, configModel.data_source_mode:', configModel.value.data_source_mode);
  const success = await handleSaveConfig();
  if (success) {
    message.success('API 与数据源配置已成功保存！');
    console.log('[ApiDataSourcePage] AFTER save & fetch, configModel.data_source_mode:', configModel.value.data_source_mode);
  } else {
    message.error(configError.value || 'API 与数据源配置保存失败。');
  }
};
</script>
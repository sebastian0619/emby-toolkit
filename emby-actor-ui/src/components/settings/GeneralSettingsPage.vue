<template>
  <n-form :model="configModel" label-placement="top" style="margin-top:15px;">
    <n-grid :cols="1" :x-gap="24">
      <n-form-item-grid-item label="处理项目间的延迟 (秒)" path="delay_between_items_sec">
        <n-input-number v-model:value="configModel.delay_between_items_sec" :min="0" :step="0.1" placeholder="例如: 0.5" />
      </n-form-item-grid-item>
      <n-form-item-grid-item label="豆瓣API默认冷却时间 (秒)" path="api_douban_default_cooldown_seconds">
        <n-input-number v-model:value="configModel.api_douban_default_cooldown_seconds" :min="0.1" :step="0.1" placeholder="例如: 1.0" />
      </n-form-item-grid-item>

      <n-form-item-grid-item path="min_score_for_review">
        <template #label>
          <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
            <span>需手动处理的最低评分阈值</span>
            <n-button size="small" type="primary" @click="savePageConfig" :loading="savingConfig" style="margin-left: 10px;">
              保存通用设置
            </n-button>
          </div>
        </template>
        <n-input-number v-model:value="configModel.min_score_for_review" :min="0" :max="10" :step="0.1" placeholder="例如: 6.0" />
        <n-text depth="3" style="font-size:0.8em; margin-top:3px;">处理质量评分低于此值的项目将进入待复核列表。</n-text>
      </n-form-item-grid-item>
    </n-grid>
  </n-form>
</template>

<script setup>
import {
  NForm, NFormItemGridItem, NInputNumber, NGrid, NText,
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

const savePageConfig = async () => {
  const success = await handleSaveConfig();
  if (success) {
    message.success('通用设置已成功保存！');
  } else {
    message.error(configError.value || '通用设置保存失败。');
  }
};
</script>
<template>
  <n-form :model="configModel" label-placement="top" style="margin-top:15px;">
    <n-grid :cols="1" :x-gap="24">
      <h4>全量扫描定时任务</h4>
      <n-form-item-grid-item>
        <n-checkbox v-model:checked="configModel.schedule_enabled">启用定时全量扫描</n-checkbox>
      </n-form-item-grid-item>
      <n-form-item-grid-item label="CRON表达式 (全量扫描)" path="schedule_cron">
        <n-input v-model:value="configModel.schedule_cron" :disabled="!configModel.schedule_enabled" placeholder="例如: 0 3 * * * (每天凌晨3点)" />
      </n-form-item-grid-item>
      <n-form-item-grid-item>
        <n-checkbox v-model:checked="configModel.schedule_force_reprocess" :disabled="!configModel.schedule_enabled">定时任务强制重处理所有项目</n-checkbox>
      </n-form-item-grid-item>

      <n-divider />

      <h4>同步人物映射表定时任务</h4>
      <n-form-item-grid-item>
          <n-checkbox v-model:checked="configModel.schedule_sync_map_enabled">启用定时同步人物映射表</n-checkbox>
      </n-form-item-grid-item>

      <n-form-item-grid-item path="schedule_sync_map_cron">
        <template #label>
          <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
            <span>CRON表达式 (同步映射表)</span>
            <n-button size="small" type="primary" @click="savePageConfig" :loading="savingConfig" style="margin-left: 10px;">
              保存定时任务配置
            </n-button>
          </div>
        </template>
        <n-input v-model:value="configModel.schedule_sync_map_cron" :disabled="!configModel.schedule_sync_map_enabled" placeholder="例如: 0 1 * * * (每天凌晨1点)" />
      </n-form-item-grid-item>
    </n-grid>
  </n-form>
</template>

<script setup>
import {
  NForm, NFormItemGridItem, NInput, NCheckbox, NDivider, NGrid,
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
    message.success('定时任务配置已成功保存！');
  } else {
    message.error(configError.value || '定时任务配置保存失败。');
  }
};
</script>
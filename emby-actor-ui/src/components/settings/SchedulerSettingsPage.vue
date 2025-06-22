<template>
  <n-space vertical :size="24" style="margin-top: 15px;">
    <!-- 卡片 1: 全量扫描 -->
    <n-card title="全量扫描定时任务" class="beautified-card" :bordered="false">
      <template #header-extra>
        <n-switch v-model:value="configModel.schedule_enabled" />
      </template>
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1" :y-gap="18">
          <n-form-item-grid-item label="CRON表达式" path="schedule_cron">
            <n-input v-model:value="configModel.schedule_cron" :disabled="!configModel.schedule_enabled" placeholder="例如: 0 3 * * *" />
          </n-form-item-grid-item>
          <n-form-item-grid-item>
            <n-checkbox v-model:checked="configModel.schedule_force_reprocess" :disabled="!configModel.schedule_enabled">
              定时任务强制重处理所有项目
            </n-checkbox>
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>

    <!-- 卡片 2: 同步映射表 -->
    <n-card title="同步演员映射表定时任务" class="beautified-card" :bordered="false">
      <template #header-extra>
        <n-switch v-model:value="configModel.schedule_sync_map_enabled" />
      </template>
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1">
          <n-form-item-grid-item label="CRON表达式" path="schedule_sync_map_cron">
            <n-input v-model:value="configModel.schedule_sync_map_cron" :disabled="!configModel.schedule_sync_map_enabled" placeholder="例如: 0 1 * * * (每天凌晨1点)" />
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>

    <!-- 卡片 3: 智能追剧 -->
    <n-card title="智能追剧更新定时任务" class="beautified-card" :bordered="false">
      <template #header-extra>
        <n-tooltip trigger="hover">
          <template #trigger>
            <n-switch v-model:value="configModel.schedule_watchlist_enabled" :disabled="!configModel.use_sa_mode" />
          </template>
          <span v-if="!configModel.use_sa_mode">
            此功能仅在“神医模式”下可用。请先在“基础设置”中启用。
          </span>
          <span v-else>
            启用/禁用智能追剧更新定时任务
          </span>
        </n-tooltip>
      </template>
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1">
          <n-form-item-grid-item label="CRON表达式" path="schedule_watchlist_cron">
            <n-input 
              v-model:value="configModel.schedule_watchlist_cron" 
              :disabled="!configModel.schedule_watchlist_enabled" 
              placeholder="例如: 0 */6 * * * (每6小时)" 
            />
            <template #feedback>
              高频率地检查追剧列表中的剧集是否有更新。
            </template>
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>

    <!-- 保存按钮 -->
    <n-button size="medium" type="primary" @click="savePageConfig" :loading="savingConfig" block>
      保存定时任务配置
    </n-button>
  </n-space>
</template>

<script setup>
import { watch } from 'vue';
import {
  NForm, NFormItemGridItem, NInput, NCheckbox, NGrid,
  NButton, NCard, NSpace, NSwitch, NTooltip,
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

// 【重构版】为所有任务的CRON输入框添加自动清理逻辑
const tasksToWatch = [
  { enabledKey: 'schedule_enabled', cronKey: 'schedule_cron' },
  { enabledKey: 'schedule_sync_map_enabled', cronKey: 'schedule_sync_map_cron' },
  { enabledKey: 'schedule_watchlist_enabled', cronKey: 'schedule_watchlist_cron' }
];

tasksToWatch.forEach(({ enabledKey, cronKey }) => {
  watch(
    () => configModel.value[enabledKey],
    (newValue) => {
      if (newValue === false) {
        configModel.value[cronKey] = '';
      }
    }
  );
});

const savePageConfig = async () => {
  const success = await handleSaveConfig();
  if (success) {
    message.success('定时任务配置已成功保存！');
  } else {
    message.error(configError.value || '定时任务配置保存失败。');
  }
};
</script>
<template>
  <!-- 最外层使用 n-space 来管理 Grid 和下方按钮的垂直间距 -->
  <n-space vertical :size="24" style="margin-top: 15px;">
    
    <!-- 使用 n-grid 实现响应式两列布局 -->
    <n-grid cols="1 s:2" :x-gap="24" :y-gap="24" responsive="screen">
      
      <!-- 卡片 1: 全量扫描 -->
      <n-gi>
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
      </n-gi>

      <!-- 卡片 2: 同步映射表 -->
      <n-gi>
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
      </n-gi>

      <!-- 卡片 3: 智能追剧 -->
      <n-gi>
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
      </n-gi>
      
      <!-- 卡片 4: 演员补充外部ID -->
      <n-gi>
        <n-card title="演员补充外部ID定时任务" class="beautified-card" :bordered="false">
          <template #header-extra>
            <n-switch v-model:value="configModel.schedule_enrich_aliases_enabled" />
          </template>
          <n-form :model="configModel" label-placement="top">
            <n-grid :cols="1">
              <n-form-item-grid-item label="CRON表达式" path="schedule_enrich_aliases_cron">
                <n-input 
                  v-model:value="configModel.schedule_enrich_aliases_cron" 
                  :disabled="!configModel.schedule_enrich_aliases_enabled" 
                  placeholder="例如: 30 2 * * * (每天凌晨2:30)" 
                />
                <template #feedback>
                  在后台扫描数据库，为缺少别名、ImdbID的演员从TMDb补充信息。这是一个耗时操作，建议在服务器空闲时执行。
                </template>
              </n-form-item-grid-item>
            </n-grid>
          </n-form>
        </n-card>
      </n-gi>

    </n-grid>

    <!-- 保存按钮 -->
    <n-button size="medium" type="primary" @click="savePageConfig" :loading="savingConfig" block>
      保存定时任务配置
    </n-button>
  </n-space>
</template>

<script setup>
import { watch } from 'vue';
import {
  NForm, NFormItemGridItem, NInput, NCheckbox, NGrid, NGi, // 添加了 NGi
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

// 【重构版】为所有任务的CRON输入框添加自动清理逻辑 (这部分逻辑无需改动)
const tasksToWatch = [
  { enabledKey: 'schedule_enabled', cronKey: 'schedule_cron' },
  { enabledKey: 'schedule_sync_map_enabled', cronKey: 'schedule_sync_map_cron' },
  { enabledKey: 'schedule_enrich_aliases_enabled', cronKey: 'schedule_enrich_aliases_cron' },
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
<template>
  <n-layout content-style="padding: 24px;">
  <!-- 最外层使用 n-space 来管理 Grid 和下方按钮的垂直间距 -->
  <n-space vertical :size="24" style="margin-top: 15px;">
    
    <!-- 使用 n-grid 实现响应式两列布局 -->
    <n-grid cols="1 s:2" :x-gap="24" :y-gap="24" responsive="screen">
      
      <!-- 卡片 1: 全量扫描 (无改动) -->
      <n-gi>
        <n-card title="全量扫描定时任务" class="glass-section" :bordered="false">
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
                  定时任务强制重处理所有项目 (将清空已处理记录)
                </n-checkbox>
              </n-form-item-grid-item>
            </n-grid>
          </n-form>
        </n-card>
      </n-gi>

      <!-- 卡片 2: 同步映射表 (无改动) -->
      <n-gi>
        <n-card title="同步演员映射表定时任务" class="glass-section" :bordered="false">
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

      <!-- 卡片 3: 智能追剧 (无改动) -->
      <n-gi>
        <n-card title="智能追剧更新定时任务" class="glass-section" :bordered="false">
          <template #header-extra>
            <n-tooltip trigger="hover">
              <template #trigger>
                <n-switch v-model:value="configModel.schedule_watchlist_enabled" />
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
      
      <!-- 卡片 4: 演员补充外部ID (无改动) -->
      <n-gi>
        <n-card title="演员补充外部ID定时任务" class="glass-section" :bordered="false">
          <template #header-extra>
            <n-switch v-model:value="configModel.schedule_enrich_aliases_enabled" />
          </template>
          <n-form :model="configModel" label-placement="top">
            <n-grid :cols="3" :x-gap="12">
              <n-gi>
                <n-form-item-grid-item label="CRON表达式" path="schedule_enrich_aliases_cron">
                  <n-input 
                    v-model:value="configModel.schedule_enrich_aliases_cron" 
                    :disabled="!configModel.schedule_enrich_aliases_enabled" 
                    placeholder="例如: 30 2 * * *" 
                  />
                </n-form-item-grid-item>
              </n-gi>
              <n-gi>
                <n-form-item-grid-item label="每次运行时长 (分钟)" path="schedule_enrich_run_duration_minutes">
                  <n-input-number
                    v-model:value="configModel.schedule_enrich_run_duration_minutes"
                    :disabled="!configModel.schedule_enrich_aliases_enabled"
                    :min="0"
                    :step="60"
                    placeholder="0 表示不限制"
                    style="width: 100%;"
                  >
                    <template #suffix>分钟</template>
                  </n-input-number>
                </n-form-item-grid-item>
              </n-gi>
              <n-gi>
                <n-form-item-grid-item label="同步冷却时间 (天)" path="schedule_enrich_sync_interval_days">
                  <template #label>
                    同步冷却时间 (天)
                    <n-tooltip trigger="hover">
                      <template #trigger>
                        <n-icon :component="Info24Regular" class="ms-1 align-middle" />
                      </template>
                      设置在多少天内不重复检查同一个演员。对于新数据库，可设置为 0 以立即处理所有演员。
                    </n-tooltip>
                  </template>
                  <n-input-number
                    v-model:value="configModel.schedule_enrich_sync_interval_days"
                    :disabled="!configModel.schedule_enrich_aliases_enabled"
                    :min="0"
                    :step="1"
                    placeholder="建议值为 7"
                    style="width: 100%;"
                  >
                    <template #suffix>天</template>
                  </n-input-number>
                </n-form-item-grid-item>
              </n-gi>
            </n-grid>
            <template #feedback>
              <n-text depth="3" style="font-size:0.8em;">
                在后台扫描数据库，为缺少别名、ImdbID的演员补充信息。这是一个耗时操作，建议在服务器空闲时执行。
                <br/>
                <strong>设置一个大于0的“每次运行时长”，任务到点后会自动停止，下次从断点继续。设为0则不限制时长。</strong>
              </n-text>
            </template>
          </n-form>
        </n-card>
      </n-gi>

      <!-- ✨✨✨ 新增卡片：演员名翻译查漏补缺 ✨✨✨ -->
      <n-gi>
        <n-card title="演员名翻译查漏补缺" class="glass-section" :bordered="false">
          <template #header-extra>
            <n-switch v-model:value="configModel.schedule_actor_cleanup_enabled" />
          </template>
          <n-form :model="configModel" label-placement="top">
            <n-grid :cols="1">
              <n-form-item-grid-item label="CRON表达式" path="schedule_actor_cleanup_cron">
                <n-input 
                  v-model:value="configModel.schedule_actor_cleanup_cron" 
                  :disabled="!configModel.schedule_actor_cleanup_enabled" 
                  placeholder="例如: 0 4 * * * (每天凌晨4点)" 
                />
                <template #feedback>
                  自动翻译Emby中所有非中文的演员名，用于修正处理流程中的“漏网之鱼”。
                </template>
              </n-form-item-grid-item>
            </n-grid>
          </n-form>
        </n-card>
      </n-gi>
      <!-- ✨✨✨ 新增卡片结束 ✨✨✨ -->

    </n-grid>

    <!-- 保存按钮 (无改动) -->
    <n-button size="medium" type="primary" @click="savePageConfig" :loading="savingConfig" block>
      保存定时任务配置
    </n-button>
  </n-space>
  </n-layout>
</template>

<script setup>
import { watch } from 'vue';
import {
  NForm, NFormItemGridItem, NInput, NCheckbox, NGrid, NGi,
  NButton, NCard, NSpace, NSwitch, NTooltip, NInputNumber, NIcon, NText, // 确保导入了所有用到的组件
  useMessage
} from 'naive-ui';
import { Info24Regular } from '@vicons/fluent'; // 确保导入了图标
import { useConfig } from '../../composables/useConfig.js';

const message = useMessage();

const {
    configModel,
    handleSaveConfig,
    savingConfig,
    configError
} = useConfig();

const tasksToWatch = [
  { enabledKey: 'schedule_enabled', cronKey: 'schedule_cron' },
  { enabledKey: 'schedule_sync_map_enabled', cronKey: 'schedule_sync_map_cron' },
  { enabledKey: 'schedule_enrich_aliases_enabled', cronKey: 'schedule_enrich_aliases_cron' },
  { enabledKey: 'schedule_watchlist_enabled', cronKey: 'schedule_watchlist_cron' },
  // ✨✨✨ 新增：将新任务加入自动清理逻辑 ✨✨✨
  { enabledKey: 'schedule_actor_cleanup_enabled', cronKey: 'schedule_actor_cleanup_cron' }
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
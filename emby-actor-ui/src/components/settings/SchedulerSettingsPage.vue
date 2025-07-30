<template>
  <n-layout content-style="padding: 24px;">
    <!-- 保留加载状态 -->
    <div v-if="isLoading" class="center-container">
      <n-spin size="large" />
    </div>
    
    <n-space v-else-if="configModel" vertical :size="24" style="margin-top: 15px;">
      
      <n-grid cols="1 l:2" :x-gap="24" :y-gap="24" responsive="screen">
        
        <!-- 卡片 1: 常规任务 -->
        <n-gi>
          <n-card title="常规任务" class="glass-section" :bordered="false">
            <div class="task-list-container">

              <!-- 任务 1.1: 全量扫描 -->
              <div class="task-item">
                <n-space align="center" justify="space-between">
                  <n-text strong>全量扫描</n-text>
                  <n-switch v-model:value="configModel.schedule_enabled" />
                </n-space>
                <n-form :model="configModel" label-placement="left" label-width="80" class="mt-3" :show-feedback="false">
                  <n-form-item label="" path="schedule_cron">
                    <n-input v-model:value="configModel.schedule_cron" :disabled="!configModel.schedule_enabled" placeholder="例如: 0 3 * * *" />
                  </n-form-item>
                  <n-form-item label="选项">
                    <n-space align="center" justify="space-between" style="width: 100%;">
                      <n-checkbox v-model:checked="configModel.schedule_force_reprocess" :disabled="!configModel.schedule_enabled">
                        强制重处理
                        <n-tooltip trigger="hover">
                          <template #trigger>
                            <n-icon :component="Info24Regular" class="ms-1 align-middle" style="cursor: help;" />
                          </template>
                          定时任务强制重处理所有项目 (将清空已处理记录)
                        </n-tooltip>
                      </n-checkbox>
                      <n-button size="small" type="primary" ghost @click="triggerTaskNow('full-scan')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                        <template #icon><n-icon :component="Play24Regular" /></template>
                        立即执行
                      </n-button>
                    </n-space>
                  </n-form-item>
                </n-form>
              </div>

              <!-- 任务 1.2: 同步演员映射表 -->
              <div class="task-item">
                <n-space align="center" justify="space-between">
                  <n-text strong>同步演员映射表</n-text>
                  <n-switch v-model:value="configModel.schedule_sync_map_enabled" />
                </n-space>
                <n-form :model="configModel" label-placement="left" label-width="80" class="mt-3">
                  <n-form-item label="" path="schedule_sync_map_cron">
                    <n-input-group>
                      <n-input v-model:value="configModel.schedule_sync_map_cron" :disabled="!configModel.schedule_sync_map_enabled" placeholder="例如: 0 1 * * * (每天凌晨1点)" />
                      <n-button type="primary" ghost @click="triggerTaskNow('sync-person-map')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                        <template #icon><n-icon :component="Play24Regular" /></template>
                        立即执行
                      </n-button>
                    </n-input-group>
                  </n-form-item>
                </n-form>
              </div>

              <!-- 任务 1.3: 演员名翻译 -->
              <div class="task-item">
                <n-space align="center" justify="space-between">
                  <n-text strong>演员名翻译</n-text>
                  <n-switch v-model:value="configModel.schedule_actor_cleanup_enabled" />
                </n-space>
                <n-form :model="configModel" label-placement="left" label-width="80" class="mt-3">
                  <n-form-item label="" path="schedule_actor_cleanup_cron">
                    <n-input-group>
                      <n-input v-model:value="configModel.schedule_actor_cleanup_cron" :disabled="!configModel.schedule_actor_cleanup_enabled" placeholder="例如: 0 4 * * * (每天凌晨4点)" />
                      <n-button type="primary" ghost @click="triggerTaskNow('actor-cleanup')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                        <template #icon><n-icon :component="Play24Regular" /></template>
                        立即执行
                      </n-button>
                    </n-input-group>
                    <template #feedback>自动翻译Emby中所有非中文的演员名。</template>
                  </n-form-item>
                </n-form>
              </div>

              <!-- 任务 1.4: 演员元数据补充 -->
              <div class="task-item">
                <n-space align="center" justify="space-between">
                  <n-text strong>演员元数据补充</n-text>
                  <n-switch v-model:value="configModel.schedule_enrich_aliases_enabled" />
                </n-space>
                <n-form :model="configModel" label-placement="top" class="mt-3">
                  <n-grid :cols="3" :x-gap="12" align-items="end">
                    <n-gi>
                      <n-form-item-grid-item label="" path="schedule_enrich_aliases_cron">
                        <n-input v-model:value="configModel.schedule_enrich_aliases_cron" :disabled="!configModel.schedule_enrich_aliases_enabled" placeholder="例如: 30 2 * * *" />
                      </n-form-item-grid-item>
                    </n-gi>
                    <n-gi>
                      <n-form-item-grid-item label="运行时长(分)" path="schedule_enrich_run_duration_minutes">
                        <n-input-number v-model:value="configModel.schedule_enrich_run_duration_minutes" :disabled="!configModel.schedule_enrich_aliases_enabled" :min="0" :step="60" placeholder="0不限制" style="width: 100%;" />
                      </n-form-item-grid-item>
                    </n-gi>
                    <n-gi>
                      <n-form-item-grid-item path="schedule_enrich_sync_interval_days">
                        <template #label>
                          <n-space :size="4" align="center">
                            <n-text>冷却(天)</n-text>
                            <n-tooltip trigger="hover">
                              <template #trigger><n-icon :component="Info24Regular" style="cursor: help;" /></template>
                              设置在多少天内不重复检查同一个演员。
                            </n-tooltip>
                          </n-space>
                        </template>
                        <n-input-number v-model:value="configModel.schedule_enrich_sync_interval_days" :disabled="!configModel.schedule_enrich_aliases_enabled" :min="0" :step="1" placeholder="建议7" style="width: 100%;" />
                      </n-form-item-grid-item>
                    </n-gi>
                  </n-grid>
                </n-form>
                <n-space justify="end">
                  <n-button size="small" type="primary" ghost @click="triggerTaskNow('enrich-aliases')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                    <template #icon><n-icon :component="Play24Regular" /></template>
                    立即执行
                  </n-button>
                </n-space>
              </div>

            </div>
          </n-card>
        </n-gi>

        <!-- 卡片 2: 订阅与刷新 -->
        <n-gi>
          <n-card title="订阅与刷新" class="glass-section" :bordered="false">
            <div class="task-list-container">

              <!-- 任务 2.1: 智能追剧刷新 -->
              <div class="task-item">
                <n-space align="center" justify="space-between">
                  <n-text strong>智能追剧刷新</n-text>
                  <n-switch v-model:value="configModel.schedule_watchlist_enabled" />
                </n-space>
                <n-form :model="configModel" label-placement="left" label-width="80" class="mt-3">
                  <n-form-item label="" path="schedule_watchlist_cron">
                    <n-input-group>
                      <n-input v-model:value="configModel.schedule_watchlist_cron" :disabled="!configModel.schedule_watchlist_enabled" placeholder="例如: 0 */6 * * * (每6小时)" />
                      <n-button type="primary" ghost @click="triggerTaskNow('process-watchlist')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                        <template #icon><n-icon :component="Play24Regular" /></template>
                        立即执行
                      </n-button>
                    </n-input-group>
                    <template #feedback>检查智能追剧列表中的剧集是否有更新。</template>
                  </n-form-item>
                </n-form>
              </div>

              <!-- 任务 2.2: 智能订阅 -->
              <div class="task-item">
                <n-space align="center" justify="space-between">
                  <n-text strong>智能订阅</n-text>
                  <n-switch v-model:value="configModel.schedule_autosub_enabled" />
                </n-space>
                <n-form :model="configModel" label-placement="left" label-width="80" class="mt-3">
                  <n-form-item label="" path="schedule_autosub_cron">
                    <n-input-group>
                      <n-input v-model:value="configModel.schedule_autosub_cron" :disabled="!configModel.schedule_autosub_enabled" placeholder="例如: 0 5 * * *" />
                      <n-button type="primary" ghost @click="triggerTaskNow('auto-subscribe')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                        <template #icon><n-icon :component="Play24Regular" /></template>
                        立即执行
                      </n-button>
                    </n-input-group>
                    <template #feedback>自动订阅缺失的电影合集和追更的剧集。</template>
                  </n-form-item>
                </n-form>
              </div>

              <!-- 任务 2.3: 电影合集刷新 -->
              <div class="task-item">
                <n-space align="center" justify="space-between">
                  <n-text strong>电影合集刷新</n-text>
                  <n-switch v-model:value="configModel.schedule_refresh_collections_enabled" />
                </n-space>
                <n-form :model="configModel" label-placement="left" label-width="80" class="mt-3">
                  <n-form-item label="" path="schedule_refresh_collections_cron">
                    <n-input-group>
                      <n-input v-model:value="configModel.schedule_refresh_collections_cron" :disabled="!configModel.schedule_refresh_collections_enabled" placeholder="例如: 0 2 * * *" />
                      <n-button type="primary" ghost @click="triggerTaskNow('refresh-collections')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                        <template #icon><n-icon :component="Play24Regular" /></template>
                        立即执行
                      </n-button>
                    </n-input-group>
                    <template #feedback>定时检查所有电影合集的缺失情况。</template>
                  </n-form-item>
                </n-form>
              </div>

              <!-- 任务 2.4: 演员订阅 -->
              <div class="task-item">
                <n-space align="center" justify="space-between">
                  <n-text strong>演员订阅</n-text>
                  <n-switch v-model:value="configModel.schedule_actor_tracking_enabled" />
                </n-space>
                <n-form :model="configModel" label-placement="left" label-width="80" class="mt-3">
                  <n-form-item label="" path="schedule_actor_tracking_cron">
                    <n-input-group>
                      <n-input v-model:value="configModel.schedule_actor_tracking_cron" :disabled="!configModel.schedule_actor_tracking_enabled" placeholder="例如: 0 5 * * *" />
                      <n-button type="primary" ghost @click="triggerTaskNow('actor-tracking')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                        <template #icon><n-icon :component="Play24Regular" /></template>
                        立即执行
                      </n-button>
                    </n-input-group>
                    <template #feedback>定时扫描所有已订阅演员的作品，检查更新并订阅缺失项。</template>
                  </n-form-item>
                </n-form>
              </div>

            </div>
          </n-card>
        </n-gi>

      </n-grid>

      <!-- 保存按钮 -->
      <n-button size="medium" type="primary" @click="savePageConfig" :loading="savingConfig" block>
        保存定时任务配置
      </n-button>
    </n-space>
  </n-layout>
</template>

<script setup>
import { watch, ref } from 'vue';
import {
  NForm, NFormItem, NFormItemGridItem, NInput, NCheckbox, NGrid, NGi,
  NButton, NCard, NSpace, NSwitch, NTooltip, NInputNumber, NIcon, NText,
  useMessage, NLayout, NSpin, NInputGroup
} from 'naive-ui';
import { Info24Regular, Play24Regular } from '@vicons/fluent';
import { useConfig } from '../../composables/useConfig.js';
import { useTaskStatus } from '../../composables/useTaskStatus.js';
import axios from 'axios';

const message = useMessage();

const {
    configModel,
    loadingConfig: isLoading,
    handleSaveConfig,
    savingConfig,
    configError
} = useConfig();

const {
    isBackgroundTaskRunning
} = useTaskStatus();

const tasksToWatch = [
  { enabledKey: 'schedule_enabled', cronKey: 'schedule_cron' },
  { enabledKey: 'schedule_sync_map_enabled', cronKey: 'schedule_sync_map_cron' },
  { enabledKey: 'schedule_enrich_aliases_enabled', cronKey: 'schedule_enrich_aliases_cron' },
  { enabledKey: 'schedule_watchlist_enabled', cronKey: 'schedule_watchlist_cron' },
  { enabledKey: 'schedule_actor_cleanup_enabled', cronKey: 'schedule_actor_cleanup_cron' },
  { enabledKey: 'schedule_autosub_enabled', cronKey: 'schedule_autosub_cron' },
  { enabledKey: 'schedule_refresh_collections_enabled', cronKey: 'schedule_refresh_collections_cron' },
  { enabledKey: 'schedule_actor_tracking_enabled', cronKey: 'schedule_actor_tracking_cron' }
];

watch(isLoading, (loading) => {
  if (loading === false && configModel.value) {
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
  }
}, { immediate: true });

const savePageConfig = async () => {
  const success = await handleSaveConfig();
  if (success) {
    message.success('定时任务配置已成功保存！');
  } else {
    message.error(configError.value || '定时任务配置保存失败。');
  }
};

const isTriggeringTask = ref(false);

const triggerTaskNow = async (taskIdentifier) => {
  if (isBackgroundTaskRunning.value) {
    message.warning('已有后台任务正在运行，请稍后再试。');
    return;
  }

  isTriggeringTask.value = true;
  try {
    const response = await axios.post(`/api/tasks/trigger/${taskIdentifier}`);
    if (response.data.status === 'success') {
      message.success(`任务 "${response.data.task_name}" 已成功提交！`);
    } else {
      message.error(response.data.message || '提交任务失败。');
    }
  } catch (error) {
    message.error('请求后端接口失败，请检查网络或后台服务。');
  } finally {
    isTriggeringTask.value = false;
  }
};
</script>

<style scoped>
/* 简单的 margin-top 辅助类 */
.mt-3 {
  margin-top: 12px;
}

/* 用于居中显示加载动画的容器样式 */
.center-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: calc(100vh - 200px);
}

/* 任务项样式 */
.task-item {
  padding-bottom: 16px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--n-border-color);
}
.task-list-container > .task-item:last-of-type {
  border-bottom: none;
  margin-bottom: 0;
  padding-bottom: 0;
}
</style>
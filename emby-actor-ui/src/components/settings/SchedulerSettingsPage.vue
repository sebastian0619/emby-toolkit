<template>
  <n-layout content-style="padding: 24px;">
    <n-space vertical :size="24" style="margin-top: 15px;">
      
      <n-grid cols="1 s:2" :x-gap="24" :y-gap="24" responsive="screen">
        
        <!-- 卡片 1: 全量扫描 -->
        <n-gi>
          <n-card title="全量扫描" class="glass-section" :bordered="false" style="height: 100%;">
            <template #header-extra>
              <n-switch v-model:value="configModel.schedule_enabled" />
            </template>
            <n-form :model="configModel" label-placement="top">
              <n-grid cols="1 s:2" :x-gap="24" responsive="screen">
                <n-form-item-grid-item label="CRON表达式" path="schedule_cron">
                  <n-input v-model:value="configModel.schedule_cron" :disabled="!configModel.schedule_enabled" placeholder="例如: 0 3 * * *" />
                </n-form-item-grid-item>
                <n-form-item-grid-item>
                  <template #label> </template>
                  <n-checkbox v-model:checked="configModel.schedule_force_reprocess" :disabled="!configModel.schedule_enabled">
                    强制重处理
                  </n-checkbox>
                  <n-tooltip trigger="hover">
                    <template #trigger>
                      <n-icon :component="Info24Regular" class="ms-1 align-middle" style="cursor: help;" />
                    </template>
                    定时任务强制重处理所有项目 (将清空已处理记录)
                  </n-tooltip>
                </n-form-item-grid-item>
              </n-grid>
            </n-form>
            <!-- ✨ 立即执行按钮 ✨ -->
            <template #action>
              <n-space justify="end">
                <n-button size="small" type="primary" ghost @click="triggerTaskNow('full-scan')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                  <template #icon><n-icon :component="Play24Regular" /></template>
                  立即执行一次
                </n-button>
              </n-space>
            </template>
          </n-card>
        </n-gi>

        <!-- 卡片 2: 同步映射表 -->
        <n-gi>
          <n-card title="同步演员映射表" class="glass-section" :bordered="false" style="height: 100%;">
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
            <!-- ✨ 立即执行按钮 ✨ -->
            <template #action>
              <n-space justify="end">
                <n-button size="small" type="primary" ghost @click="triggerTaskNow('sync-person-map')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                  <template #icon><n-icon :component="Play24Regular" /></template>
                  立即执行一次
                </n-button>
              </n-space>
            </template>
          </n-card>
        </n-gi>
        <!-- 卡片 5: 演员名翻译查漏补缺 -->
        <n-gi>
          <n-card title="演员名翻译" class="glass-section" :bordered="false" style="height: 100%;">
            <template #header-extra>
              <n-switch v-model:value="configModel.schedule_actor_cleanup_enabled" />
            </template>
            <n-form :model="configModel" label-placement="top">
              <n-grid :cols="1">
                <n-form-item-grid-item label="CRON表达式" path="schedule_actor_cleanup_cron">
                  <n-input v-model:value="configModel.schedule_actor_cleanup_cron" :disabled="!configModel.schedule_actor_cleanup_enabled" placeholder="例如: 0 4 * * * (每天凌晨4点)" />
                  <template #feedback>自动翻译Emby中所有非中文的演员名。</template>
                </n-form-item-grid-item>
              </n-grid>
            </n-form>
            <!-- ✨ 立即执行按钮 (修正了 taskIdentifier) ✨ -->
            <template #action>
              <n-space justify="end">
                <n-button size="small" type="primary" ghost @click="triggerTaskNow('actor-cleanup')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                  <template #icon><n-icon :component="Play24Regular" /></template>
                  立即执行一次
                </n-button>
              </n-space>
            </template>
          </n-card>
        </n-gi>        
        <!-- 卡片 4: 演员元数据增强 -->
        <n-gi>
          <n-card title="演员元数据补充" class="glass-section" :bordered="false" style="height: 100%;">
            <template #header-extra>
              <n-switch v-model:value="configModel.schedule_enrich_aliases_enabled" />
            </template>
            <n-form :model="configModel" label-placement="top">
              <n-grid :cols="3" :x-gap="12">
                <n-gi><n-form-item-grid-item label="CRON表达式" path="schedule_enrich_aliases_cron"><n-input v-model:value="configModel.schedule_enrich_aliases_cron" :disabled="!configModel.schedule_enrich_aliases_enabled" placeholder="例如: 30 2 * * *" /></n-form-item-grid-item></n-gi>
                <n-gi><n-form-item-grid-item label="每次运行时长 (分钟)" path="schedule_enrich_run_duration_minutes"><n-input-number v-model:value="configModel.schedule_enrich_run_duration_minutes" :disabled="!configModel.schedule_enrich_aliases_enabled" :min="0" :step="60" placeholder="0 表示不限制" style="width: 100%;"><template #suffix>分钟</template></n-input-number></n-form-item-grid-item></n-gi>
                <n-gi><n-form-item-grid-item path="schedule_enrich_sync_interval_days"><template #label>同步冷却时间 (天)<n-tooltip trigger="hover"><template #trigger><n-icon :component="Info24Regular" class="ms-1 align-middle" /></template>设置在多少天内不重复检查同一个演员。对于新数据库，可设置为 0 以立即处理所有演员。</n-tooltip></template><n-input-number v-model:value="configModel.schedule_enrich_sync_interval_days" :disabled="!configModel.schedule_enrich_aliases_enabled" :min="0" :step="1" placeholder="建议值为 7" style="width: 100%;"><template #suffix>天</template></n-input-number></n-form-item-grid-item></n-gi>
              </n-grid>
              <template #feedback><n-text depth="3" style="font-size:0.8em;">在后台扫描数据库，为演员补充IMDbID、头像等信息。这是一个耗时操作，建议在服务器空闲时执行。<br/><strong>设置一个大于0的“每次运行时长”，任务到点后会自动停止，下次从断点继续。设为0则不限制时长。</strong></n-text></template>
            </n-form>
            <!-- ✨ 立即执行按钮 ✨ -->
            <template #action>
              <n-space justify="end">
                <n-button size="small" type="primary" ghost @click="triggerTaskNow('enrich-aliases')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                  <template #icon><n-icon :component="Play24Regular" /></template>
                  立即执行一次
                </n-button>
              </n-space>
            </template>
          </n-card>
        </n-gi>
        <!-- 卡片 5: 剧集简介更新 -->
        <n-gi>
          <n-card title="智能追剧刷新" class="glass-section" :bordered="false" style="height: 100%;">
            <template #header-extra>
              <n-tooltip trigger="hover">
                <template #trigger><n-switch v-model:value="configModel.schedule_watchlist_enabled" /></template>
                <span>启用/禁用追更剧集刷新定时任务</span>
              </n-tooltip>
            </template>
            <n-form :model="configModel" label-placement="top">
              <n-grid :cols="1">
                <n-form-item-grid-item label="CRON表达式" path="schedule_watchlist_cron">
                  <n-input v-model:value="configModel.schedule_watchlist_cron" :disabled="!configModel.schedule_watchlist_enabled" placeholder="例如: 0 */6 * * * (每6小时)" />
                  <template #feedback>检查智能追剧列表中的剧集是否有更新。</template>
                </n-form-item-grid-item>
              </n-grid>
            </n-form>
            <!-- ✨ 立即执行按钮 ✨ -->
            <template #action>
              <n-space justify="end">
                <n-button size="small" type="primary" ghost @click="triggerTaskNow('process-watchlist')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                  <template #icon><n-icon :component="Play24Regular" /></template>
                  立即执行一次
                </n-button>
              </n-space>
            </template>
          </n-card>
        </n-gi>

        
        <!-- ★★★ 卡片 6: 智能订阅 ★★★ -->
        <n-gi>
          <n-card title="智能订阅" class="glass-section" :bordered="false" style="height: 100%;">
            <template #header-extra><n-switch v-model:value="configModel.schedule_autosub_enabled" /></template>
            <n-form :model="configModel" label-placement="top">
              <n-grid :cols="1">
                <n-form-item-grid-item label="CRON表达式" path="schedule_autosub_cron">
                  <n-input v-model:value="configModel.schedule_autosub_cron" :disabled="!configModel.schedule_autosub_enabled" placeholder="例如: 0 5 * * *" />
                  <template #feedback>自动订阅缺失的电影合集和追更的剧集。</template>
                </n-form-item-grid-item>
              </n-grid>
            </n-form>
            <template #action><n-space justify="end"><n-button size="small" type="primary" ghost @click="triggerTaskNow('auto-subscribe')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning"><template #icon><n-icon :component="Play24Regular" /></template>立即执行一次</n-button></n-space></template>
          </n-card>
        </n-gi>
        <!-- ★★★ 电影合集刷新任务卡片 ★★★ -->
        <n-gi>
          <n-card title="电影合集刷新" class="glass-section" :bordered="false" style="height: 100%;">
            <template #header-extra><n-switch v-model:value="configModel.schedule_refresh_collections_enabled" /></template>
            <n-form :model="configModel" label-placement="top">
              <n-grid :cols="1">
                <n-form-item-grid-item label="CRON表达式" path="schedule_refresh_collections_cron">
                  <n-input v-model:value="configModel.schedule_refresh_collections_cron" :disabled="!configModel.schedule_refresh_collections_enabled" placeholder="例如: 0 2 * * *" />
                  <template #feedback>定时检查所有电影合集的缺失情况。</template>
                </n-form-item-grid-item>
              </n-grid>
            </n-form>
            <template #action><n-space justify="end"><n-button size="small" type="primary" ghost @click="triggerTaskNow('refresh-collections')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning"><template #icon><n-icon :component="Play24Regular" /></template>立即执行一次</n-button></n-space></template>
          </n-card>
        </n-gi>
        <!-- ★★★ 新增卡片: 演员订阅扫描 ★★★ -->
        <n-gi>
          <n-card title="演员订阅" class="glass-section" :bordered="false" style="height: 100%;">
            <template #header-extra>
              <n-switch v-model:value="configModel.schedule_actor_tracking_enabled" />
            </template>
            <n-form :model="configModel" label-placement="top">
              <n-grid :cols="1">
                <n-form-item-grid-item label="CRON表达式" path="schedule_actor_tracking_cron">
                  <n-input v-model:value="configModel.schedule_actor_tracking_cron" :disabled="!configModel.schedule_actor_tracking_enabled" placeholder="例如: 0 5 * * *" />
                  <template #feedback>定时扫描所有已订阅演员的作品，检查更新并订阅缺失项。</template>
                </n-form-item-grid-item>
              </n-grid>
            </n-form>
            <template #action>
              <n-space justify="end">
                <n-button size="small" type="primary" ghost @click="triggerTaskNow('actor-tracking')" :loading="isTriggeringTask" :disabled="isBackgroundTaskRunning">
                  <template #icon><n-icon :component="Play24Regular" /></template>
                  立即执行一次
                </n-button>
              </n-space>
            </template>
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
// 脚本部分与你提供的版本完全相同，无需改动
import { watch, ref } from 'vue';
import {
  NForm, NFormItemGridItem, NInput, NCheckbox, NGrid, NGi,
  NButton, NCard, NSpace, NSwitch, NTooltip, NInputNumber, NIcon, NText,
  useMessage
} from 'naive-ui';
import { Info24Regular, Play24Regular } from '@vicons/fluent';
import { useConfig } from '../../composables/useConfig.js';
import { useTaskStatus } from '../../composables/useTaskStatus.js';
import axios from 'axios';

const message = useMessage();

const {
    configModel,
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
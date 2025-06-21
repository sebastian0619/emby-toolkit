<template>
  <n-space vertical :size="24" style="margin-top: 15px;">
    <!-- 卡片 1: 全量扫描 (保持不变) -->
    <n-card title="全量扫描定时任务" class="beautified-card" :bordered="false">
      <template #header-extra>
        <n-switch v-model:value="configModel.schedule_enabled">
          <template #checked>已启用</template>
          <template #unchecked>已禁用</template>
        </n-switch>
      </template>
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1" :y-gap="18">
          <n-form-item-grid-item label="CRON表达式" path="schedule_cron">
            <n-input v-model:value="configModel.schedule_cron" :disabled="!configModel.schedule_enabled" placeholder="例如: 0 3 * * * (每天凌晨3点)" />
          </n-form-item-grid-item>
          <n-form-item-grid-item>
            <n-checkbox v-model:checked="configModel.schedule_force_reprocess" :disabled="!configModel.schedule_enabled">
              定时任务强制重处理所有项目
            </n-checkbox>
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>

    <!-- 卡片 2: 同步映射表 (保持不变) -->
    <n-card title="同步演员映射表定时任务" class="beautified-card" :bordered="false">
      <template #header-extra>
        <n-switch v-model:value="configModel.schedule_sync_map_enabled">
          <template #checked>已启用</template>
          <template #unchecked>已禁用</template>
        </n-switch>
      </template>
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1">
          <n-form-item-grid-item label="CRON表达式" path="schedule_sync_map_cron">
            <n-input v-model:value="configModel.schedule_sync_map_cron" :disabled="!configModel.schedule_sync_map_enabled" placeholder="例如: 0 1 * * * (每天凌晨1点)" />
          </n-form-item-grid-item>
        </n-grid>
      </n-form>
    </n-card>

    <!-- ✨✨✨ 卡片 3: 智能追剧 (核心修改) ✨✨✨ -->
    <n-card title="智能追剧更新定时任务" class="beautified-card" :bordered="false">
      <!-- 1. 将开关移到 header-extra，与其他卡片保持一致 -->
      <template #header-extra>
        <n-switch v-model:value="configModel.schedule_watchlist_enabled" :disabled="!configModel.use_sa_mode">
          <template #checked>已启用</template>
          <template #unchecked>已禁用</template>
        </n-switch>
      </template>
      <!-- 2. 内部也使用 n-form 和 n-grid 统一布局 -->
      <n-form :model="configModel" label-placement="top">
        <n-grid :cols="1">
          <n-form-item-grid-item label="CRON表达式" path="schedule_watchlist_cron">
            <!-- 3. 使用 :disabled 替代 v-if，交互更平滑 -->
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

    <!-- 保存按钮 (保持不变) -->
    <n-button size="medium" type="primary" @click="savePageConfig" :loading="savingConfig" block>
      保存定时任务配置
    </n-button>
  </n-space>
</template>

<script setup>
// ... 你的 <script setup> 部分完全不需要任何修改 ...
// ★★★ 只需要确保从 naive-ui 导入了 NCard, NSpace, NSwitch 等组件 ★★★
import {
  NForm, NFormItemGridItem, NInput, NCheckbox, NGrid,
  NButton, NCard, NSpace, NSwitch,
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

const savePageConfig = async () => {
  const success = await handleSaveConfig();
  if (success) {
    message.success('定时任务配置已成功保存！');
  } else {
    message.error(configError.value || '定时任务配置保存失败。');
  }
};
</script>
<template>
  <!-- ★★★ 1. 使用 n-space 作为根容器 ★★★ -->
  <n-space vertical :size="24" style="margin-top: 15px;">
    <!-- ★★★ 2. 第一个卡片，包裹全量扫描定时任务 ★★★ -->
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

    <!-- ★★★ 3. 第二个卡片，包裹同步映射表定时任务 ★★★ -->
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

    <!-- ★★★ 4. 页面底部的保存按钮 ★★★ -->
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
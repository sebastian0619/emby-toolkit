<template>
  <div>
    <!-- ★★★ 1. 使用 n-spin 来包裹所有内容，根据加载状态显示 ★★★ -->
    <n-spin :show="loadingConfig">
      <n-grid :x-gap="24" :y-gap="24" :cols="1">
        <!-- 修改密码组件 -->
        <n-gi v-if="authStore.isAuthEnabled">
          <ChangePassword />
        </n-gi>

        <!-- 通用设置卡片 -->
        <n-gi>
          <n-card title="通用设置" size="medium">
            <!-- ★★★ 2. 只有在 config 加载成功后才显示表单 ★★★ -->
            <n-form v-if="configModel" @submit.prevent="save" label-placement="left" label-width="auto">
              <n-form-item label="任务间延时 (秒)">
                <n-input-number v-model:value="configModel.delay_between_items_sec" :min="0" :step="0.1" style="width: 100%;" />
                <template #feedback>处理每个媒体项目之间的等待时间，避免请求过快。</template>
              </n-form-item>
              <n-form-item label="待复核最低分">
                <n-input-number v-model:value="configModel.min_score_for_review" :min="0" :max="10" :step="0.1" style="width: 100%;" />
                <template #feedback>处理质量评分低于此分数的项目将被加入待复核列表。</template>
              </n-form-item>
              <n-form-item label="处理分集">
                <n-switch v-model:value="configModel.process_episodes" />
                <template #feedback>开启后，处理电视剧时会为每一季/每一集生成单独的元数据文件。</template>
              </n-form-item>
              <n-form-item label="同步图片">
                <n-switch v-model:value="configModel.sync_images" />
                <template #feedback>开启后，处理媒体时会下载海报、横幅图等图片文件。</template>
              </n-form-item>
              <n-button type="primary" attr-type="submit" :loading="savingConfig">
                保存通用设置
              </n-button>
            </n-form>
            <!-- 如果加载失败，显示错误信息 -->
            <n-alert v-else-if="configError" title="加载配置失败" type="error">
              {{ configError }}
            </n-alert>
          </n-card>
        </n-gi>
      </n-grid>
      <template #description>正在加载配置...</template>
    </n-spin>
  </div>
</template>

<script setup>
import { onMounted } from 'vue';
import { NCard, NForm, NFormItem, NInputNumber, NSwitch, NButton, NGrid, NGi, NSpin, NAlert, useMessage } from 'naive-ui';
import { useConfig } from '../../composables/useConfig.js';
import ChangePassword from './ChangePassword.vue';
import { useAuthStore } from '../../stores/auth';

// ★★★ 3. 正确地从 useConfig 获取变量 ★★★
const { configModel, loadingConfig, savingConfig, configError, fetchConfigData, handleSaveConfig } = useConfig();
const authStore = useAuthStore();
const message = useMessage();

// ★★★ 4. 定义 save 函数来调用 handleSaveConfig ★★★
async function save() {
  const success = await handleSaveConfig();
  if (success) {
    message.success('通用设置已保存！');
  }
}

// ★★★ 5. 在组件挂载时才去获取配置数据 ★★★
onMounted(() => {
  fetchConfigData();
});
</script>
<!-- src/components/ActionsPage.vue -->
<template>
  <div>
    <n-h2>手动操作</n-h2>

    <!-- 全量扫描区域 -->
    <n-card title="全量媒体库扫描" style="margin-bottom: 20px;">
      <template #header-extra>
        <n-button
          type="error"
          size="small"
          @click="triggerStopTask"
          ghost
        >
          停止扫描
        </n-button>
      </template>

      <n-space vertical align="start">
        <n-checkbox v-model:checked="forceReprocessAll">
          强制重新处理所有项目
        </n-checkbox>
        <n-button
          type="primary"
          @click="triggerFullScan"
          :loading="isTaskRunning && currentActionIncludesScan"
          :disabled="isTaskRunning && !currentActionIncludesScan"
        >
          启动全量扫描
        </n-button>
        <p style="font-size: 0.85em; color: #888; margin-top: 5px;">注意：全量扫描可能非常耗时。</p>
      </n-space>
    </n-card>

    <!-- 同步映射表区域 -->
    <n-card title="同步Emby人物映射表" style="margin-bottom: 20px;">
      <n-button
        type="primary"
        @click="triggerSyncMap"
        :loading="isTaskRunning && currentActionIncludesSyncMap"
        :disabled="isTaskRunning && !currentActionIncludesSyncMap"
      >
        启动同步映射表
      </n-button>
      <p style="font-size: 0.85em; color: #888; margin-top: 5px;">此操作会从Emby读取所有人物信息并更新本地映射库。</p>
    </n-card>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import axios from 'axios';
import { NCard, NButton, NCheckbox, NSpace, NForm, useMessage } from 'naive-ui';

const message = useMessage();

// 这个组件需要知道当前是否有任务在运行，以禁用按钮
// 这个状态可以从 App.vue 通过 prop 传递下来，或者使用全局状态管理 (Pinia)
// 为简单起见，我们先假设有一个 prop (或者你可以直接从 App.vue 的 backgroundTaskStatus 获取)
// 但更规范的做法是事件上报给 App.vue 或通过 Pinia
const props = defineProps({
  isTaskRunning: { // 这个 prop 需要从 App.vue 传递
    type: Boolean,
    default: false
  }
});
// 或者，如果你想直接访问 App.vue 的 backgroundTaskStatus (不推荐直接跨组件访问，但作为快速方案)
// import { backgroundTaskStatus as globalStatus } from '../App.vue'; // 这通常不行，除非 App.vue 导出了它
// const isTaskRunning = computed(() => globalStatus.value.is_running);


const forceReprocessAll = ref(false);

// 这些函数会向后端发送请求，后端处理实际逻辑
const triggerFullScan = async () => {
  try {
    // 注意：HTML表单的 checkbox 如果未选中，通常不发送。
    // Flask request.form.get('force_reprocess_all') == 'on'
    // 如果用 axios 发送 JSON，需要明确发送布尔值或特定值
    const formData = new FormData(); // 使用 FormData 来模拟表单提交
    if (forceReprocessAll.value) {
        formData.append('force_reprocess_all', 'on');
    }
    // 或者发送 JSON: await axios.post('/trigger_full_scan', { force_reprocess_all: forceReprocessAll.value });
    // 这取决于你的 Flask 后端如何接收参数

    await axios.post('/api/trigger_full_scan', formData); // 假设 Flask 端用 request.form
    message.success('全量扫描任务已启动！');
  } catch (error) {
    console.error('启动全量扫描失败:', error);
    message.error('启动全量扫描失败，请查看日志。');
  }
};

const triggerSyncMap = async () => {
  try {
    await axios.post('/api/trigger_sync_person_map');
    message.success('同步人物映射表任务已启动！');
  } catch (error) {
    console.error('启动同步映射表失败:', error);
    message.error('启动同步映射表失败，请查看日志。');
  }
};

const triggerStopTask = async () => {
  try {
    await axios.post('/api/trigger_stop_task');
    message.info('已发送停止任务请求。');
  } catch (error) {
    console.error('发送停止任务请求失败:', error);
    message.error('发送停止任务请求失败，请查看日志。');
  }
};
</script>

<style scoped>
.n-card {
  margin-bottom: 20px;
}
p {
  margin-top: 8px;
}
</style>
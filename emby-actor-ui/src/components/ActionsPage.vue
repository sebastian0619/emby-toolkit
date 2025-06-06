<template>
  <div>
    <n-h2>手动操作</n-h2>

    <!-- 全量扫描区域 -->
    <n-card title="全量媒体库扫描">
      <template #header-extra>
        <n-button
          type="error"
          size="small"
          @click="triggerStopTask"
          :disabled="!taskStatus.is_running"
          ghost
        >
          停止当前任务
        </n-button>
      </template>

      <n-space vertical align="start">
        <n-checkbox v-model:checked="forceReprocessAll" :disabled="taskStatus.is_running">
          强制重新处理所有项目 (将清除已处理记录)
        </n-checkbox>
        <n-button
          type="primary"
          @click="triggerFullScan"
          :loading="taskStatus.is_running && currentActionIncludesScan"
          :disabled="taskStatus.is_running && !currentActionIncludesScan"
        >
          启动全量扫描
        </n-button>
        <p style="font-size: 0.85em; color: #888; margin-top: 5px;">注意：全量扫描可能非常耗时。</p>
      </n-space>
    </n-card>

    <!-- 同步映射表区域 -->
    <n-card title="同步Emby演员映射表">
      <n-space vertical>
        <n-space align="center">
          <!-- 原有的同步按钮 -->
          <n-button
            type="primary"
            @click="triggerSyncMap"
            :loading="taskStatus.is_running && currentActionIncludesSyncMap"
            :disabled="taskStatus.is_running && !currentActionIncludesSyncMap"
          >
            同步映射表
          </n-button>

          <!-- 新增的导出按钮 -->
          <n-button @click="exportMap" :loading="isExporting">
            <template #icon><n-icon :component="ExportIcon" /></template>
            导出映射表
          </n-button>
          
          <!-- 新增的导入按钮 -->
          <n-upload
            action="/api/import_person_map"
            :show-file-list="false"
            @before-upload="beforeImport"
            @finish="afterImport"
            @error="errorImport"
            accept=".csv"
          >
            <n-button :loading="isImporting">
              <template #icon><n-icon :component="ImportIcon" /></template>
              导入映射表
            </n-button>
          </n-upload>
        </n-space>
        
        <!-- 说明文字 -->
        <p style="font-size: 0.85em; color: #888; margin: 0;">
          <b>同步：</b>从Emby读取所有人物信息为后续创建各数据源ID映射表。<br>
          <b>导出/导入：</b>用于备份或迁移人物映射数据，以及分享给别人使用。导入会覆盖现有相同 Emby Person ID 的记录。
        </p>
      </n-space>
    </n-card>

    <!-- 实时日志显示区域 -->
    <n-card title="实时日志" content-style="padding: 0;" style="margin-top: 20px;">
       <template #header-extra>
        <n-button text @click="clearLogs" style="font-size: 14px;">
          <template #icon><n-icon :component="TrashIcon" /></template>
          清空日志
        </n-button>
      </template>
      <n-log
        :log="logContent"
        trim
        :rows="15"
        style="font-size: 13px; line-height: 1.6;"
      />
    </n-card>

  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import axios from 'axios';
import { 
  NCard, NButton, NCheckbox, NSpace, NH2, NLog, NIcon, useMessage, 
  NUpload 
} from 'naive-ui';

import { 
  TrashOutline as TrashIcon,
  DownloadOutline as ExportIcon, 
  CloudUploadOutline as ImportIcon 
} from '@vicons/ionicons5';

const message = useMessage();

// --- Props ---
// 接收来自 App.vue 的实时任务状态
const props = defineProps({
  taskStatus: {
    type: Object,
    required: true,
    default: () => ({
      is_running: false,
      current_action: '空闲',
      progress: 0,
      message: '无任务',
      logs: [] // 确保默认值包含 logs 数组
    })
  }
});

// --- 本地状态 ---
const forceReprocessAll = ref(false);

// --- 计算属性 ---
// ✨ logContent 现在直接从 prop 计算，不再需要本地 logs 数组和 watch 监听器 ✨
const logContent = computed(() => {
  // 如果 taskStatus.logs 存在且是一个数组，则用换行符连接
  if (props.taskStatus && Array.isArray(props.taskStatus.logs)) {
    return props.taskStatus.logs.slice().reverse().join('\n');
  }
  return '等待日志...'; // 默认或错误情况下的文本
});

const currentActionIncludesScan = computed(() => 
  props.taskStatus.current_action && props.taskStatus.current_action.toLowerCase().includes('scan')
);
const currentActionIncludesSyncMap = computed(() => 
  props.taskStatus.current_action && props.taskStatus.current_action.toLowerCase().includes('sync')
);

// ---导出/导入映射表
const isExporting = ref(false);
const isImporting = ref(false);

// --- 方法 ---
const triggerFullScan = async () => {
  try {
    const formData = new FormData();
    if (forceReprocessAll.value) {
        formData.append('force_reprocess_all', 'on');
    }
    await axios.post('/api/trigger_full_scan', formData);
    message.success('全量扫描任务已启动！');
  } catch (error) {
    console.error('启动全量扫描失败:', error);
    message.error(error.response?.data?.error || '启动全量扫描失败，请查看日志。');
  }
};

const triggerSyncMap = async () => {
  try {
    await axios.post('/api/trigger_sync_person_map');
    message.success('同步人物映射表任务已启动！');
  } catch (error) {
    console.error('启动同步映射表失败:', error);
    message.error(error.response?.data?.error || '启动同步映射表失败，请查看日志。');
  }
};

const triggerStopTask = async () => {
  try {
    await axios.post('/api/trigger_stop_task');
    message.info('已发送停止任务请求。');
  } catch (error) {
    console.error('发送停止任务请求失败:', error);
    message.error(error.response?.data?.error || '发送停止任务请求失败，请查看日志。');
  }
};

const clearLogs = () => {
  // 这个按钮现在只是一个视觉效果，因为日志由后端控制
  message.info('日志将在下次任务开始时自动清空。');
};

// --- Watcher (不再需要了！) ---
// 由于 logContent 直接从 prop 计算，我们不再需要复杂的 watch 逻辑来手动拼接日志。
// 每次 App.vue 的轮询更新了 taskStatus prop，logContent 就会自动重新计算。
// 这大大简化了组件的逻辑！
// 3. 添加新的方法
const exportMap = async () => {
  isExporting.value = true;
  try {
    // 使用 axios 下载文件
    const response = await axios({
      url: '/api/export_person_map',
      method: 'GET',
      responseType: 'blob', // 关键：告诉 axios 我们要下载二进制数据
    });

    // 从响应头中获取文件名
    const contentDisposition = response.headers['content-disposition'];
    let filename = 'person_map_backup.csv'; // 默认文件名
    if (contentDisposition) {
      const filenameMatch = contentDisposition.match(/filename="?(.+)"?/);
      if (filenameMatch.length === 2)
        filename = filenameMatch[1];
    }

    // 创建一个临时的 a 标签来触发下载
    const url = window.URL.createObjectURL(new Blob([response.data]));
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);

    message.success('映射表已开始导出下载！');
  } catch (error) {
    console.error('导出失败:', error);
    message.error('导出映射表失败，请查看日志。');
  } finally {
    isExporting.value = false;
  }
};

const beforeImport = () => {
  isImporting.value = true;
  message.loading('正在上传并导入文件，请稍候...', { duration: 0 });
  return true; // 返回 true 以继续上传
};

const afterImport = ({ event }) => {
  isImporting.value = false;
  message.destroyAll(); // 清除 loading 消息
  try {
    const response = JSON.parse(event.target.response);
    message.success(response.message || '导入成功！');
  } catch (e) {
    message.error('导入成功，但无法解析服务器响应。');
  }
};

const errorImport = ({ event }) => {
  isImporting.value = false;
  message.destroyAll();
  try {
    const response = JSON.parse(event.target.response);
    message.error(response.error || '导入失败，未知错误。');
  } catch (e) {
    message.error('导入失败，并且无法解析服务器错误响应。');
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
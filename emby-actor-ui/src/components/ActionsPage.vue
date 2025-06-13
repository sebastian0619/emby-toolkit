<template>
  <!-- ★★★ 1. 使用 n-space 作为根容器 ★★★ -->
  <n-space vertical :size="24" style="margin-top: 15px;">
    <n-alert 
      v-if="taskStatus.is_running" 
      title="后台任务运行中" 
      type="warning" 
      closable
    >
      当前有后台任务正在执行 ({{ taskStatus.current_action }})。
      在此期间，任何需要写入数据库的操作都可能会失败。
      建议等待当前任务完成后再进行其他操作。
    </n-alert>

    <!-- ★★★ 2. 第一个卡片，包裹全量扫描区域 ★★★ -->
    <n-card title="全量媒体库扫描" class="beautified-card" :bordered="false">
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
        <p style="font-size: 0.85em; color: var(--n-text-color-3); margin: 0;">友情提示：全量扫描耗时较长，大约3-5分钟才能处理一部影片，建议定时夜里悄悄的干活。</p>
      </n-space>
    </n-card>

    <!-- ★★★ 3. 第二个卡片，包裹同步映射表区域 ★★★ -->
    <n-card title="同步Emby演员映射表" class="beautified-card" :bordered="false">
      <n-space vertical>
        <n-checkbox v-model:checked="forceFullSyncMap" :disabled="taskStatus.is_running">
          强制全量同步 (会合并所有记录，耗时较长)
        </n-checkbox>
        
        <!-- ✨✨✨ 检查这个 n-space 是否完整 ✨✨✨ -->
        <n-space align="center">
          <!-- 按钮 1: 同步 -->
          <n-button
            type="primary"
            @click="triggerSyncMap"
            :loading="taskStatus.is_running && currentActionIncludesSyncMap"
            :disabled="taskStatus.is_running && !currentActionIncludesSyncMap"
          >
            {{ forceFullSyncMap ? '启动全量同步' : '启动增量同步' }}
          </n-button>

          <!-- ✨✨✨ 按钮 2: 导出 (检查这里) ✨✨✨ -->
          <n-button @click="exportMap" :loading="isExporting">
            <template #icon><n-icon :component="ExportIcon" /></template>
            导出
          </n-button>
          
          <!-- ✨✨✨ 按钮 3: 导入 (检查这里) ✨✨✨ -->
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
              导入
            </n-button>
          </n-upload>
        </n-space>
        
        <p style="font-size: 0.85em; color: var(--n-text-color-3); margin: 0;">
          <b>同步：</b>从Emby读取所有人物信息为后续创建各数据源ID映射表。<br>
          <b>导出/导入：</b>用于备份或迁移人物映射数据。导入会覆盖现有相同 Emby Person ID 的记录。
        </p>
      </n-space>
    </n-card>

    <!-- ★★★ 4. 第三个卡片，包裹实时日志显示区域 ★★★ -->
    <n-card title="实时日志" class="beautified-card" :bordered="false" content-style="padding: 0;">
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
  </n-space>
</template>

<script setup>
// ... 你的 <script setup> 部分完全不需要任何修改 ...
// ★★★ 只需要确保从 naive-ui 导入了 NCard 和 NSpace ★★★
import { ref, computed } from 'vue';
import axios from 'axios';
import { 
  NCard, NButton, NCheckbox, NSpace, NAlert, NLog, NIcon, useMessage, 
  NUpload 
} from 'naive-ui';

import { 
  TrashOutline as TrashIcon,
  DownloadOutline as ExportIcon, 
  CloudUploadOutline as ImportIcon 
} from '@vicons/ionicons5';

const message = useMessage();

const props = defineProps({
  taskStatus: {
    type: Object,
    required: true,
    default: () => ({
      is_running: false,
      current_action: '空闲',
      progress: 0,
      message: '无任务',
      logs: []
    })
  }
});

const forceReprocessAll = ref(false);
const forceFullSyncMap = ref(false);
const isExporting = ref(false);
const isImporting = ref(false);

const logContent = computed(() => {
  if (props.taskStatus && Array.isArray(props.taskStatus.logs)) {
    return props.taskStatus.logs.slice().reverse().join('\n');
  }
  return '等待日志...';
});

const currentActionIncludesScan = computed(() => 
  props.taskStatus.current_action && props.taskStatus.current_action.toLowerCase().includes('scan')
);
const currentActionIncludesSyncMap = computed(() => 
  props.taskStatus.current_action && props.taskStatus.current_action.toLowerCase().includes('sync')
);

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
    // ✨ 2. 准备要发送的数据
    const payload = {
      full_sync: forceFullSyncMap.value
    };
    // ✨ 3. 在 post 请求中发送数据
    await axios.post('/api/trigger_sync_person_map', payload);
    
    const messageText = forceFullSyncMap.value ? '全量同步任务已启动！' : '增量同步任务已启动！';
    message.success(messageText);
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
  message.info('日志将在下次任务开始时自动清空。');
};

const exportMap = async () => {
  isExporting.value = true;
  try {
    const response = await axios({
      url: '/api/export_person_map',
      method: 'GET',
      responseType: 'blob',
    });

    const contentDisposition = response.headers['content-disposition'];
    let filename = 'person_map_backup.csv';
    if (contentDisposition) {
      const filenameMatch = contentDisposition.match(/filename="?(.+)"?/);
      if (filenameMatch.length === 2)
        filename = filenameMatch[1];
    }

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
  return true;
};

const afterImport = ({ event }) => {
  isImporting.value = false;
  message.destroyAll();
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

<!-- ★★★ 5. 移除旧的 scoped 样式 ★★★ -->
<!-- 原来的 <style scoped> 已被删除，因为卡片间距由 n-space 控制 -->
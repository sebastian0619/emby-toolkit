<!-- src/components/ActionsPage.vue -->

<template>
  <n-space vertical :size="24" style="margin-top: 15px;">
  <div class="actions-page-container">
    
    <n-grid cols="1 l:2" :x-gap="24" :y-gap="24" responsive="screen">
      
      <!-- 左侧列：任务操作区 -->
      <n-gi span="1">
        <n-space vertical :size="24">
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

          <!-- 卡片1: 全量扫描 -->
          <n-card title="全量媒体库扫描" class="glass-section" :bordered="false">
            <!-- 使用 n-space 来实现灵活的对齐 -->
            <n-space vertical size="large">

              <!-- 上半部分：选项和说明 -->
              <n-checkbox v-model:checked="forceReprocessAll" :disabled="taskStatus.is_running">
                强制重新处理所有项目 (将清空已处理记录)
              </n-checkbox>
              <p style="font-size: 0.85em; color: var(--n-text-color-3); margin: 0; padding-bottom: 12px;">
                <b>强制重处理：</b>会忽略已处理记录，对媒体库所有项目重新执行核心逻辑。<br>
                <b>清除缓存：</b>用于解决数据循环污染问题，建议在勾选“强制重处理”后、启动扫描前执行。
              </p>

              <n-divider style="margin: 0;" />

              <!-- 下半部分：操作按钮区 -->
              <n-space align="center" justify="space-between" style="width: 100%;">
                
                <!-- 左侧的操作按钮 -->
                <n-space>
                  <n-button
                    type="primary"
                    @click="triggerFullScan"
                    :loading="taskStatus.is_running && currentActionIncludesScan"
                    :disabled="taskStatus.is_running && !currentActionIncludesScan"
                  >
                    启动全量扫描
                  </n-button>
                  
                  <!-- ★★★ 找回来的停止任务按钮 ★★★ -->
                  <n-button
                    type="error"
                    @click="triggerStopTask"
                    :disabled="!taskStatus.is_running"
                    ghost
                  >
                    停止当前任务
                  </n-button>
                </n-space>

                <!-- ★★★ 右侧的清除缓存按钮 ★★★ -->
                <n-popconfirm
                  @positive-click="handleClearCaches"
                  positive-text="我确定，清空！"
                  negative-text="算了"
                  :positive-button-props="{ type: 'error' }"
                >
                  <template #trigger>
                    <n-button 
                      type="error" 
                      text 
                      :loading="isClearing"
                      :disabled="taskStatus.is_running"
                      style="font-size: 13px;"
                    >
                      <template #icon><n-icon :component="TrashIcon" /></template>
                      清除TMDb缓存
                    </n-button>
                  </template>
                  您确定要删除所有TMDb相关的缓存和覆盖文件吗？<br>
                  这将强制下次处理时从网络重新获取所有数据。<br>
                  <strong>此操作不可恢复！</strong>
                </n-popconfirm>

              </n-space>

            </n-space>
          </n-card>

          <!-- 卡片2: 同步映射表 -->
          <n-card title="同步Emby演员映射表" class="glass-section" :bordered="false">
            <n-space vertical>
              <n-space align="center">
                <n-button
                  type="primary"
                  @click="triggerSyncMap"
                  :loading="taskStatus.is_running && currentActionIncludesSyncMap"
                  :disabled="taskStatus.is_running && !currentActionIncludesSyncMap"
                >
                  启动同步
                </n-button>
                
                <!-- ★★★ 新增：一键重构按钮 ★★★ -->
                <n-button
                  type="warning"
                  @click="triggerRebuildActors"
                  :loading="taskStatus.is_running && currentActionIncludesRebuild"
                  :disabled="taskStatus.is_running && !currentActionIncludesRebuild"
                  ghost
                >
                  重构演员库
                </n-button>
                <!-- ★★★ 新增结束 ★★★ -->

                <n-button @click="exportMap" :loading="isExporting" class="action-button">
                  <template #icon><n-icon :component="ExportIcon" /></template>
                  导出
                </n-button>
                <n-upload
                  action="/api/actors/import"
                  :show-file-list="false"
                  @before-upload="beforeImport"
                  @finish="afterImport"
                  @error="errorImport"
                  accept=".csv"
                >
                  <n-button :loading="isImporting" class="action-button">
                    <template #icon><n-icon :component="ImportIcon" /></template>
                    导入
                  </n-button>
                </n-upload>
              </n-space>
              <p style="font-size: 0.85em; color: var(--n-text-color-3); margin: 0;">
                <b>同步：</b>读取所有演员信息为后续创建各数据源ID映射表。<br>
                <!-- ★★★ 新增：对重构功能的说明 ★★★ -->
                <b>一键重构：</b><span style="color: var(--n-warning-color);">【高危】</span>清空并重建Emby演员数据库，解决数据污染、索引损坏等疑难杂症。<br>
                <b>导出/导入：</b>用于备份或迁移演员映射数据。导入会追加更新现有演员的记录。
              </p>
            </n-space>
          </n-card>
          <n-card title="全量海报同步" class="glass-section" :bordered="false">
            <n-space vertical>
              <p style="font-size: 0.85em; color: var(--n-text-color-3); margin: 0;">
                此功能会遍历所有**已处理**的媒体，将 Emby 中的海报、背景图等同步到 override 缓存目录。
              </p>
              <n-button
                type="primary"
                @click="triggerFullImageSync"
                :loading="taskStatus.is_running && currentActionIncludesImageSync"
                :disabled="taskStatus.is_running && !currentActionIncludesImageSync"
              >
                开始同步
              </n-button>
            </n-space>
          </n-card>
        </n-space>
      </n-gi>

      <!-- 右侧列：实时日志区 -->
      <n-gi span="1">
        <n-card title="实时日志" class="glass-section" :bordered="false" content-style="padding: 0;">
          <!-- ★★★ 1. 在卡片标题栏添加一个按钮 ★★★ -->
          <template #header-extra>
            <n-button text @click="isLogViewerVisible = true" title="查看历史归档日志">
              <template #icon><n-icon :component="DocumentTextOutline" /></template>
              历史日志
            </n-button>
          </template>
          <n-log
            ref="logRef"
            :log="logContent"
            trim
            :rows="30"
            style="font-size: 13px; line-height: 1.6;"
          />
        </n-card>
      </n-gi>
    </n-grid>
    <LogViewer v-model:show="isLogViewerVisible" />
  </div>
  </n-space>
</template>

<script setup>
// ★★★ 变化点2: 重新引入 nextTick，因为简化后的 watch 仍然需要它 ★★★
import { ref, computed, watch, nextTick } from 'vue';
import LogViewer from './LogViewer.vue';
import axios from 'axios';
import { 
  NCard, NButton, NCheckbox, NSpace, NAlert, NLog, NIcon, useMessage, 
  NUpload, NGrid, NGi,
  useDialog, NPopconfirm,
} from 'naive-ui';
import { 
  TrashOutline as TrashIcon,
  DownloadOutline as ExportIcon, 
  CloudUploadOutline as ImportIcon,
  DocumentTextOutline 
} from '@vicons/ionicons5';

// --- Refs and Props ---
const logRef = ref(null);
const isClearing = ref(false);
const message = useMessage();
const dialog = useDialog();
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
const isExporting = ref(false);
const isImporting = ref(false);
const isLogViewerVisible = ref(false);

// --- Computed Properties (保持不变) ---
const logContent = computed(() => {
  if (props.taskStatus && Array.isArray(props.taskStatus.logs)) {
    return props.taskStatus.logs.join('\n');
  }
  return '等待日志...';
});

const currentActionIncludesScan = computed(() => 
  props.taskStatus.current_action && props.taskStatus.current_action.toLowerCase().includes('scan')
);
const currentActionIncludesSyncMap = computed(() => 
  props.taskStatus.current_action && props.taskStatus.current_action.toLowerCase().includes('sync')
);
const currentActionIncludesRebuild = computed(() => 
  props.taskStatus.current_action && props.taskStatus.current_action.toLowerCase().includes('rebuild')
);
const currentActionIncludesImageSync = computed(() => 
  props.taskStatus.current_action && props.taskStatus.current_action.toLowerCase().includes('image_sync')
);

// ★★★ 变化点4: 使用一个极其简化的 watch 监听器，实现无条件自动滚动 ★★★
watch(() => props.taskStatus.logs, async () => {
  // 等待 Vue 将新的日志内容渲染到 DOM 上
  await nextTick();
  
  // 只要 log 组件存在，就滚动到底部
  if (logRef.value) {
    logRef.value.scrollTo({ position: 'bottom', slient: true });
  }
}, { deep: true });


// --- Methods ---

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
    await axios.post('/api/trigger_sync_person_map', {});
    message.success('同步任务已启动！');
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

// ★★★ 变化点5: 移除了 clearLogs 函数 ★★★

const exportMap = async () => {
  isExporting.value = true;
  try {
    const response = await axios({
      url: '/api/actors/export',
      method: 'GET',
      responseType: 'blob',
    });

    const contentDisposition = response.headers['content-disposition'];
    let filename = 'person_map_backup.csv';
    if (contentDisposition) {
      const match = contentDisposition.match(/filename="?(.+?)"?$/);
      if (match?.[1]) filename = match[1];
    }

    const blobUrl = window.URL.createObjectURL(new Blob([response.data]));
    const link = document.createElement('a');
    link.href = blobUrl;
    link.setAttribute('download', filename);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(blobUrl);

    message.success('映射表已开始导出下载！');
  } catch (err) {
    console.error('导出失败:', err);
    message.error('导出映射表失败，请查看日志。');
  } finally {
    isExporting.value = false;
  }
};

const beforeImport = () => {
  isImporting.value = true;
  message.loading('正在上传并处理文件...', { duration: 0 });
  return true;
};

const afterImport = ({ event }) => {
  isImporting.value = false;
  message.destroyAll();
  try {
    const response = JSON.parse(event?.target?.response ?? '{}');
    if (response?.message) {
      message.success(response.message);
    } else {
      message.error('导入完成，但响应无明确信息。');
    }
  } catch (e) {
    message.error('导入成功，但无法解析服务器响应。');
  }
};

const errorImport = ({ event }) => {
  isImporting.value = false;
  message.destroyAll();
  try {
    const response = JSON.parse(event?.target?.response ?? '{}');
    message.error(response?.error || '导入失败，未知错误。');
  } catch (e) {
    message.error('导入失败，并且无法解析服务器错误响应。');
  }
};

const triggerFullImageSync = async () => {
  try {
    const response = await axios.post('/api/actions/trigger_full_image_sync');
    message.success(response.data.message || '全量海报同步任务已启动！');
  } catch (error) {
    console.error('启动全量海报同步失败:', error);
    message.error(error.response?.data?.error || '启动任务失败，请查看日志。');
  }
};

const triggerRebuildActors = () => {
  dialog.warning({
    title: '高危操作确认',
    content: '此操作将彻底清空并重建Emby中的所有演员数据，用于解决数据污染、索引损坏等疑难杂症。过程可能需要较长时间，期间请勿关闭浏览器。您确定要继续吗？',
    positiveText: '我意已决，开始重构',
    negativeText: '我再想想',
    onPositiveClick: async () => {
      try {
        const response = await axios.post('/api/tasks/rebuild-actors');
        message.success(response.data.message || '重构任务已成功提交！');
      } catch (error) {
        console.error('启动重构任务失败:', error);
        message.error(error.response?.data?.message || '启动任务失败，请查看日志。');
      }
    },
  });
};

const handleClearCaches = async () => {
  isClearing.value = true;
  message.info("正在发送清除TMDb缓存指令...");
  try {
    const response = await axios.post('/api/actions/clear_tmdb_caches');
    message.success(response.data.message || "TMDb缓存已成功清除！");
  } catch (error) {
    console.error("清除缓存失败:", error);
    message.error(error.response?.data?.message || "清除缓存失败，请检查后端日志。");
  } finally {
    isClearing.value = false;
  }
};
</script>
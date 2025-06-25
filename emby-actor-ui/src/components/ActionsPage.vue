<!-- src/components/ActionsPage.vue -->

<template>
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
              <p style="font-size: 0.85em; color: var(--n-text-color-3); margin: 0;">建议定时夜里悄悄的干活。</p>
            </n-space>
          </n-card>

          <!-- 卡片2: 同步映射表 -->
          <n-card title="同步Emby演员映射表" class="beautified-card" :bordered="false">
            <n-space vertical>
              <!-- 复选框已被删除 -->
              <n-space align="center">
                <n-button
                  type="primary"
                  @click="triggerSyncMap"
                  :loading="taskStatus.is_running && currentActionIncludesSyncMap"
                  :disabled="taskStatus.is_running && !currentActionIncludesSyncMap"
                >
                  启动同步 <!-- 按钮文本已固定 -->
                </n-button>
                <n-button @click="exportMap" :loading="isExporting">
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
                  <n-button :loading="isImporting">
                    <template #icon><n-icon :component="ImportIcon" /></template>
                    导入
                  </n-button>
                </n-upload>
              </n-space>
              <p style="font-size: 0.85em; color: var(--n-text-color-3); margin: 0;">
                <b>同步：</b>读取所有演员信息为后续创建各数据源ID映射表。<br>
                <b>导出/导入：</b>用于备份或迁移演员映射数据。导入会追加更新现有演员的记录。
              </p>
            </n-space>
          </n-card>
          <n-card title="全量海报同步" class="beautified-card" :bordered="false">
            <n-space vertical>
              <p style="font-size: 0.85em; color: var(--n-text-color-3); margin: 0;">
                此功能会遍历所有**已处理**的媒体，将你在 Emby 中设置的最新海报、背景图等同步到 override 缓存目录。
                当更换了喜欢的海报后，使用此功能可以确保它们被正确备份和应用。
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
        <n-card title="实时日志" class="beautified-card" :bordered="false" content-style="padding: 0;">
          
          <!-- ★★★ 1. 把 #header-extra 插槽放在正确的位置 ★★★ -->
          <!-- 它应该是 n-card 的直接子元素 -->
          <template #header-extra>
            <n-space align="center">
              <n-switch v-model:value="autoScrollEnabled" size="small">
                <template #checked>
                  自动滚动
                </template>
                <template #unchecked>
                  停止滚动
                </template>
              </n-switch>
              <n-button text @click="clearLogs" style="font-size: 14px; margin-left: 12px;">
                <template #icon><n-icon :component="TrashIcon" /></template>
                清空日志
              </n-button>
            </n-space>
          </template>

          <!-- ★★★ 2. 只保留一个 n-log 组件 ★★★ -->
          <!-- 并且把我们新加的 :to="logScrollToLine" 绑定加上 -->
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
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'; // 重新引入 nextTick
import axios from 'axios';
import { 
  NCard, NButton, NCheckbox, NSpace, NAlert, NLog, NIcon, useMessage, 
  NUpload, NGrid, NGi, NSwitch
} from 'naive-ui';
import { 
  TrashOutline as TrashIcon,
  DownloadOutline as ExportIcon, 
  CloudUploadOutline as ImportIcon 
} from '@vicons/ionicons5';

// --- Refs and Props (保持不变) ---
const logRef = ref(null);
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
const isExporting = ref(false);
const isImporting = ref(false);
const autoScrollEnabled = ref(true); // 我们的总开关
const logScrollToLine = ref(0);
const logContentForDisplay = ref('等待日志...');
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

// ★★★ 3. 修改 watch 监听器 ★★★
watch(() => props.taskStatus.logs, async (newLogs, oldLogs) => {
  // 如果没有新的日志内容，或者 log 组件的 ref 还不存在，则不执行任何操作
  if (!newLogs || newLogs.length === (oldLogs?.length || 0) || !logRef.value) {
    return;
  }

  // 步骤 1: 在 DOM 更新之前，检查用户是否已经滚动到底部
  // 我们需要安全地访问 Naive UI 内部的滚动条实例
  const scrollbarInst = logRef.value.scrollbarInst;
  if (!scrollbarInst || !scrollbarInst.containerRef) {
    // 如果无法获取滚动条实例，为了安全起见，直接使用之前的简单逻辑
    if (autoScrollEnabled.value) {
      await nextTick();
      if (logRef.value) {
        logRef.value.scrollTo({ position: 'bottom', slient: true });
      }
    }
    return;
  }
  
  const scrollEl = scrollbarInst.containerRef;
  // 判断是否在底部，我们给一个小的容差（比如 10px），让判断更宽松
  const isScrolledToBottom = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight <= 10;

  // 步骤 2: 等待 Vue 完成 DOM 更新
  await nextTick();

  // 步骤 3: 应用我们最终的智能滚动规则
  // 只有在“自动滚动”开关为开，并且用户在更新前就已经在底部时，我们才执行滚动
  if (autoScrollEnabled.value && isScrolledToBottom) {
    logRef.value.scrollTo({ position: 'bottom', slient: true });
  }
  // 在所有其他情况下（开关关闭，或者用户已经向上滚动），我们什么都不做。

}, { deep: true });


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
    // 不再需要构建 payload，发送一个空对象或不发送 body 即可
    await axios.post('/api/trigger_sync_person_map', {});
    
    // 消息文本已固定
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

const clearLogs = () => {
  message.info('日志将在下次任务开始时自动清空。');
};

const exportMap = async () => {
  isExporting.value = true;
  try {
    const response = await axios({
      url: '/api/actors/export', // ✅ 改为统一接口
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
// ★★★ 新增：触发全量图片同步的函数 ★★★
const triggerFullImageSync = async () => {
  try {
    const response = await axios.post('/api/actions/trigger_full_image_sync');
    message.success(response.data.message || '全量海报同步任务已启动！');
  } catch (error) {
    console.error('启动全量海报同步失败:', error);
    message.error(error.response?.data?.error || '启动任务失败，请查看日志。');
  }
};
</script>
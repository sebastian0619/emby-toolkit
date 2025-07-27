<!-- src/components/ActionsPage.vue (V8 - 最终自适应高度修正版) -->
<template>
  <n-layout content-style="padding: 24px;">
  <n-space vertical :size="24" style="margin-top: 15px;">
    <div class="actions-page-container">
      <n-grid cols="1 l:2" :x-gap="24" :y-gap="24" responsive="screen">
        <!-- 左侧列 -->
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

            <!-- 卡片 1: 全量媒体库扫描 -->
            <n-card title="全量媒体库扫描" class="glass-section" :bordered="false">
              <n-space vertical size="large">
                <n-checkbox v-model:checked="forceReprocessAll" :disabled="taskStatus.is_running">
                  强制重新处理所有项目 (将清空已处理记录)
                </n-checkbox>
                <p class="description-text">
                  <b>强制重处理：</b>会忽略已处理记录，对媒体库所有项目重新执行核心逻辑。<br>
                  <b>清除TMDb缓存：</b>用于解决数据循环污染问题，非必要不要操作此选项。
                </p>
                <n-divider style="margin: 0;" />
                <n-space align="center" justify="space-between" style="width: 100%;">
                  <n-space>
                    <n-button
                      type="primary"
                      @click="triggerFullScan"
                      :loading="taskStatus.is_running && currentActionIncludesScan"
                      :disabled="taskStatus.is_running && !currentActionIncludesScan"
                    >
                      启动全量扫描
                    </n-button>
                    <n-button type="error" @click="triggerStopTask" :disabled="!taskStatus.is_running" ghost>
                      停止当前任务
                    </n-button>
                  </n-space>
                  <n-popconfirm
                    @positive-click="handleClearCaches"
                    positive-text="我确定，清空！"
                    negative-text="算了"
                    :positive-button-props="{ type: 'error' }"
                  >
                    <template #trigger>
                      <n-button type="error" text :loading="isClearing" :disabled="taskStatus.is_running" style="font-size: 13px;">
                        <template #icon><n-icon :component="TrashIcon" /></template>
                        清除TMDb缓存
                      </n-button>
                    </template>
                    你确定要删除所有TMDb相关的缓存和覆盖文件吗？<br>
                    这将强制下次处理时从网络重新获取所有数据。<br>
                    <strong>此操作不可恢复！</strong>
                  </n-popconfirm>
                </n-space>
              </n-space>
            </n-card>

            <!-- 卡片 2: 同步Emby演员映射表 -->
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
                  <n-button
                    type="warning"
                    @click="triggerRebuildActors"
                    :loading="taskStatus.is_running && currentActionIncludesRebuild"
                    :disabled="taskStatus.is_running && !currentActionIncludesRebuild"
                    ghost
                  >
                    重构演员库
                  </n-button>
                </n-space>
                <p class="description-text">
                  <b>同步：</b>读取所有演员信息为后续创建各数据源ID映射表。<br>
                  <b>重构演员库：</b><span class="warning-text">【高危】</span>清空并重建Emby演员数据库，解决数据污染、索引损坏等疑难杂症。
                </p>
              </n-space>
            </n-card>

            <!-- 卡片 3: 数据管理 -->
            <n-card title="数据管理 (备份与恢复)" class="glass-section" :bordered="false">
              <n-space vertical>
                <n-space align="center">
                  <n-button @click="showExportModal" :loading="isExporting" class="action-button">
                    <template #icon><n-icon :component="ExportIcon" /></template>
                    导出数据
                  </n-button>
                  <n-upload
                    :custom-request="handleCustomImportRequest"
                    :show-file-list="false"
                    accept=".json"
                  >
                    <n-button :loading="isImporting" class="action-button">
                      <template #icon><n-icon :component="ImportIcon" /></template>
                      导入数据
                    </n-button>
                  </n-upload>
                </n-space>
                <p class="description-text">
                  <b>导出：</b>将数据库中的一个或多个表备份为 JSON 文件。<br>
                  <b>导入：</b>从 JSON 备份文件中恢复数据，支持“共享合并”或“本地恢复”模式。
                </p>
              </n-space>
            </n-card>
          </n-space>
        </n-gi>

        <!-- 右侧列 -->
        <n-gi span="1" style="display: flex; flex-direction: column; gap: 24px;">
          
          <!-- 卡片 4: 全量海报同步 -->
          <n-card title="全量海报同步" class="glass-section" :bordered="false">
            <n-space vertical>
              <p class="description-text">
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

          <!-- 卡片 5: 实时日志 -->
          <n-card 
            title="实时日志" 
            class="glass-section" 
            :bordered="false" 
            style="flex-grow: 1; display: flex; flex-direction: column;" 
            content-style="flex-grow: 1; display: flex; flex-direction: column; padding: 0 24px 24px 24px;"
            header-style="padding-bottom: 12px;"
          >
            <template #header-extra>
              <n-button text @click="isLogViewerVisible = true" title="查看历史归档日志">
                <template #icon><n-icon :component="DocumentTextOutline" /></template>
                历史日志
              </n-button>
            </template>
            <n-log ref="logRef" :log="logContent" trim class="log-panel" style="flex-grow: 1;"/>
          </n-card>
        </n-gi>
      </n-grid>
      
      <!-- 导出选项模态框 -->
      <LogViewer v-model:show="isLogViewerVisible" />
      <n-modal v-model:show="exportModalVisible" preset="dialog" title="选择要导出的数据表">
        <n-space justify="end" style="margin-bottom: 10px;">
          <n-button text type="primary" @click="selectAllForExport">全选</n-button>
          <n-button text type="primary" @click="deselectAllForExport">全不选</n-button>
        </n-space>
        <n-checkbox-group v-model:value="tablesToExport" vertical>
          <n-grid :y-gap="8" :cols="2">
            <n-gi v-for="table in allDbTables" :key="table">
              <n-checkbox :value="table">
                {{ tableInfo[table]?.cn || table }}
                <span v-if="tableInfo[table]?.isSharable" class="sharable-label"> [可共享数据]</span>
              </n-checkbox>
            </n-gi>
          </n-grid>
        </n-checkbox-group>
        <template #action>
          <n-button @click="exportModalVisible = false">取消</n-button>
          <n-button type="primary" @click="handleExport" :disabled="tablesToExport.length === 0">确认导出</n-button>
        </template>
      </n-modal>

      <!-- 导入选项模态框 -->
      <n-modal v-model:show="importModalVisible" preset="dialog" title="确认导入选项">
        <n-space vertical>
          <div><p><strong>文件名:</strong> {{ fileToImport?.name }}</p></div>
          <n-form-item label="导入模式" required>
            <n-radio-group v-model:value="importOptions.mode">
              <n-space>
                <n-radio value="merge"><strong>共享合并</strong> 导入别人共享的备份，添加新数据，更新旧数据。</n-radio>
                <n-radio value="overwrite"><strong class="warning-text">本地恢复</strong> (危险!): 仅能导入自己导出的备份！！！。</n-radio>
              </n-space>
            </n-radio-group>
          </n-form-item>
          <n-form-item required>
             <template #label>
                <span>要导入的表 (从文件中自动读取)</span>
                <n-space style="margin-left: 20px;">
                  <n-button size="tiny" text type="primary" @click="selectAllForImport">全选</n-button>
                  <n-button size="tiny" text type="primary" @click="deselectAllForImport">全不选</n-button>
                </n-space>
             </template>
             <n-checkbox-group v-model:value="importOptions.tables" vertical>
                <n-grid :y-gap="8" :cols="2">
                  <n-gi v-for="table in tablesInBackupFile" :key="table">
                    <n-checkbox :value="table">
                      {{ tableInfo[table]?.cn || table }}
                      <span v-if="tableInfo[table]?.isSharable" class="sharable-label"> [可共享数据]</span>
                    </n-checkbox>
                  </n-gi>
                </n-grid>
            </n-checkbox-group>
          </n-form-item>
        </n-space>
        <template #action>
          <n-button @click="cancelImport">取消</n-button>
          <n-button type="primary" @click="confirmImport" :disabled="importOptions.tables.length === 0">开始导入</n-button>
        </template>
      </n-modal>
    </div>
  </n-space>
  </n-layout>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue';
import LogViewer from './LogViewer.vue';
import axios from 'axios';
import { 
  NCard, NButton, NCheckbox, NSpace, NAlert, NLog, NIcon, useMessage, 
  NUpload, NGrid, NGi, useDialog, NPopconfirm, NDivider, NModal,
  NCheckboxGroup, NFormItem, NRadioGroup, NRadio
} from 'naive-ui';
import { 
  TrashOutline as TrashIcon,
  DownloadOutline as ExportIcon, 
  CloudUploadOutline as ImportIcon,
  DocumentTextOutline 
} from '@vicons/ionicons5';

const tableInfo = {
  'person_identity_map': { cn: '演员身份映射表', isSharable: true },
  'ActorMetadata': { cn: '演员元数据', isSharable: true },
  'translation_cache': { cn: '翻译缓存', isSharable: true },
  'watchlist': { cn: '追剧列表', isSharable: false },
  'actor_subscriptions': { cn: '演员订阅配置', isSharable: false },
  'tracked_actor_media': { cn: '已追踪的演员作品', isSharable: false },
  'collections_info': { cn: '电影合集信息', isSharable: false },
  'processed_log': { cn: '已处理日志', isSharable: false },
  'failed_log': { cn: '待复核日志', isSharable: false },
  'users': { cn: '用户账户', isSharable: false },
};

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
const isLogViewerVisible = ref(false);

// ★★★ 核心修正：将所有与导入/导出相关的 ref 定义移到顶部 ★★★
// --- Export Logic Refs ---
const isExporting = ref(false);
const exportModalVisible = ref(false);
const allDbTables = ref([]);
const tablesToExport = ref([]);

// --- Import Logic Refs ---
const isImporting = ref(false);
const importModalVisible = ref(false);
const fileToImport = ref(null);
const tablesInBackupFile = ref([]);
const importOptions = ref({
  mode: 'merge',
  tables: [],
});

// --- Computed Properties ---
const logContent = computed(() => props.taskStatus?.logs?.join('\n') || '等待日志...');
const currentActionIncludesScan = computed(() => props.taskStatus.current_action?.toLowerCase().includes('scan'));
const currentActionIncludesSyncMap = computed(() => props.taskStatus.current_action?.toLowerCase().includes('同步'));
const currentActionIncludesRebuild = computed(() => props.taskStatus.current_action?.toLowerCase().includes('重构'));
const currentActionIncludesImageSync = computed(() => props.taskStatus.current_action?.toLowerCase().includes('海报'));

// --- Watchers ---
watch(() => props.taskStatus.logs, async () => {
  await nextTick();
  logRef.value?.scrollTo({ position: 'bottom', slient: true });
}, { deep: true });

// ★ 现在这个 watcher 可以安全地访问 importOptions 了 ★
watch(() => importOptions.value.mode, (newMode) => {
  if (importModalVisible.value) {
    if (newMode === 'merge') {
      importOptions.value.tables = tablesInBackupFile.value.filter(t => tableInfo[t]?.isSharable);
    } else {
      importOptions.value.tables = [...tablesInBackupFile.value];
    }
  }
});

// --- Methods for Existing Actions (mostly unchanged) ---
const triggerFullScan = async () => {
  try {
    const formData = new FormData();
    if (forceReprocessAll.value) {
        formData.append('force_reprocess_all', 'on');
    }
    await axios.post('/api/trigger_full_scan', formData);
    message.success('全量扫描任务已启动！');
  } catch (error) {
    message.error(error.response?.data?.error || '启动全量扫描失败，请查看日志。');
  }
};

const triggerSyncMap = async () => {
  try {
    await axios.post('/api/trigger_sync_person_map', {});
    message.success('同步任务已启动！');
  } catch (error) {
    message.error(error.response?.data?.error || '启动同步映射表失败，请查看日志。');
  }
};

const triggerStopTask = async () => {
  try {
    await axios.post('/api/trigger_stop_task');
    message.info('已发送停止任务请求。');
  } catch (error) {
    message.error(error.response?.data?.error || '发送停止任务请求失败，请查看日志。');
  }
};

const triggerFullImageSync = async () => {
  try {
    const response = await axios.post('/api/actions/trigger_full_image_sync');
    message.success(response.data.message || '全量海报同步任务已启动！');
  } catch (error) {
    message.error(error.response?.data?.error || '启动任务失败，请查看日志。');
  }
};

const triggerRebuildActors = () => {
  dialog.warning({
    title: '高危操作确认',
    content: '此操作将彻底清空并重建Emby中的所有演员数据，用于解决数据污染、索引损坏等疑难杂症。过程可能需要较长时间，期间请勿关闭浏览器。你确定要继续吗？',
    positiveText: '我意已决，开始重构',
    negativeText: '我再想想',
    onPositiveClick: async () => {
      try {
        const response = await axios.post('/api/tasks/rebuild-actors');
        message.success(response.data.message || '重构任务已成功提交！');
      } catch (error) {
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
    message.error(error.response?.data?.message || "清除缓存失败，请检查后端日志。");
  } finally {
    isClearing.value = false;
  }
};

// --- Methods for Data Management ---

// --- Export Logic ---
const showExportModal = async () => {
  try {
    const response = await axios.get('/api/database/tables');
    allDbTables.value = response.data;
    tablesToExport.value = response.data.filter(t => tableInfo[t]?.isSharable);
    exportModalVisible.value = true;
  } catch (error) {
    message.error('无法获取数据库表列表，请检查后端日志。');
  }
};

const handleExport = async () => {
  isExporting.value = true;
  exportModalVisible.value = false;
  try {
    const response = await axios.post('/api/database/export', {
      tables: tablesToExport.value
    }, {
      responseType: 'blob',
    });

    const contentDisposition = response.headers['content-disposition'];
    let filename = 'database_backup.json';
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

    message.success('数据已开始导出下载！');
  } catch (err) {
    message.error('导出数据失败，请查看日志。');
  } finally {
    isExporting.value = false;
  }
};

const selectAllForExport = () => tablesToExport.value = [...allDbTables.value];
const deselectAllForExport = () => tablesToExport.value = [];

// --- Import Logic ---
const handleCustomImportRequest = ({ file }) => {
  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const content = JSON.parse(e.target.result);
      if (!content.data || typeof content.data !== 'object') {
        message.error('备份文件格式不正确：缺少 "data" 对象。');
        return;
      }
      tablesInBackupFile.value = Object.keys(content.data);
      if (tablesInBackupFile.value.length === 0) {
        message.error('备份文件格式不正确： "data" 对象为空。');
        return;
      }
      
      if (importOptions.value.mode === 'merge') {
        importOptions.value.tables = tablesInBackupFile.value.filter(t => tableInfo[t]?.isSharable);
      } else {
        importOptions.value.tables = [...tablesInBackupFile.value];
      }
      
      fileToImport.value = file.file;
      importModalVisible.value = true;
    } catch (err) {
      message.error('无法解析JSON文件，请确保文件格式正确。');
    }
  };
  reader.readAsText(file.file);
};

const cancelImport = () => {
  importModalVisible.value = false;
  fileToImport.value = null;
};

const confirmImport = () => {
  importModalVisible.value = false; 
  startImportProcess();   
};
const startImportProcess = (force = false) => {
  isImporting.value = true;
  message.loading('正在上传并处理文件...', { duration: 0 });

  const formData = new FormData();
  formData.append('file', fileToImport.value);
  formData.append('mode', importOptions.value.mode);
  formData.append('tables', importOptions.value.tables.join(','));
  if (force) {
    // 如果是强制执行，添加特殊标记
    formData.append('force_overwrite', 'true');
  }

  axios.post('/api/database/import', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })
  .then(response => {
    isImporting.value = false;
    message.destroyAll();
    message.success(response.data?.message || '导入任务已提交！');
  })
  .catch(error => {
    isImporting.value = false;
    message.destroyAll();
    
    const errorData = error.response?.data;
    
    // ★ 核心：捕获特定警告，弹出二次确认框
    if (error.response?.status === 409 && errorData?.confirm_required) {
      dialog.warning({
        title: '高危操作确认',
        content: errorData.error, // 显示后端传来的警告信息
        positiveText: '我明白风险，继续覆盖',
        negativeText: '取消',
        positiveButtonProps: { type: 'error' },
        onPositiveClick: () => {
          // 用户确认后，带上 force 标记再次尝试
          startImportProcess(true);
        },
      });
    } else {
      // 其他普通错误，直接显示
      message.error(errorData?.error || '导入失败，未知错误。');
    }
  });
};
const selectAllForImport = () => importOptions.value.tables = [...tablesInBackupFile.value];
const deselectAllForImport = () => importOptions.value.tables = [];

</script>


<style scoped>
.actions-page-container {
  max-width: 100%;
  margin: auto;
}
.glass-section {
  background-color: rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(10px);
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.2);
}
.description-text {
  font-size: 0.85em;
  color: var(--n-text-color-3);
  margin: 0;
  line-height: 1.6;
}
.warning-text {
  color: var(--n-warning-color);
  font-weight: bold;
}
.log-panel {
  font-size: 13px;
  line-height: 1.6;
  /* ▼▼▼ 核心修改点 ▼▼▼ */
  background-color: transparent; /* 将独立的背景色改为透明 */
}
.sharable-label {
  color: var(--n-info-color-suppl);
  font-size: 0.9em;
  margin-left: 4px;
  font-weight: normal;
}
</style>
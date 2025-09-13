<!-- src/components/MediaCleanupPage.vue (模态框集成版) -->
<template>
  <n-layout content-style="padding: 24px;">
    <div class="cleanup-page">
      <n-page-header>
        <template #title>
        <n-space align="center">
            <span>重复项清理</span>
            <n-tag v-if="!isLoading" type="info" round :bordered="false" size="small">
            {{ allTasks.length }} 组待处理
            </n-tag>
        </n-space>
        </template>
        <n-alert title="操作提示" type="warning" style="margin-top: 24px;">
        本模块用于查找并清理媒体库中的“重复项”问题（多个独立的媒体项指向了同一个电影/剧集）。<br />
        如果你的重复媒体被神医插件合并，请先使用“神医”插件的“一键拆分多版本”功能，再重新扫描。<br />
        **所有清理操作都会从 Emby 和硬盘中永久删除文件，是高危操作，请谨慎使用！**
        </n-alert>
        <template #extra>
          <n-space>
            <n-dropdown 
              trigger="click"
              :options="batchActions"
              @select="handleBatchAction"
            >
              <n-button type="error" :disabled="selectedTasks.size === 0">
                批量操作 ({{ selectedTasks.size }})
              </n-button>
            </n-dropdown>
            
            <!-- ★★★ 核心修改 1/2: 这个按钮现在只负责打开新的弹窗 ★★★ -->
            <n-button @click="showSettingsModal = true">
              <template #icon><n-icon :component="SettingsIcon" /></template>
              清理规则
            </n-button>

            <n-button 
              type="primary" 
              @click="triggerScan" 
              :loading="isTaskRunning('扫描媒体去重项')"
            >
              <template #icon><n-icon :component="ScanIcon" /></template>
              扫描媒体库
            </n-button>
          </n-space>
        </template>
      </n-page-header>
      <n-divider />

      <div v-if="isLoading" class="center-container"><n-spin size="large" /></div>
      <div v-else-if="error" class="center-container"><n-alert title="加载错误" type="error">{{ error }}</n-alert></div>
      <div v-else-if="allTasks.length > 0">
        <n-data-table
          :columns="columns"
          :data="allTasks"
          :pagination="pagination"
          :row-key="row => row.id"
          v-model:checked-row-keys="selectedTaskIds"
        />
      </div>
      <div v-else class="center-container">
        <n-empty description="太棒了！没有发现任何需要清理的项目。" size="huge" />
      </div>

      <!-- ★★★ 核心修改 2/2: 使用新的、干净的 Modal 来包裹我们的独立组件 ★★★ -->
      <n-modal 
        v-model:show="showSettingsModal" 
        preset="card" 
        style="width: 90%; max-width: 700px;" 
        title="媒体去重决策规则"
        :on-after-leave="fetchData" 
      >
        <!-- ★★★ 核心修改：监听子组件的 on-close 事件 ★★★ -->
        <MediaCleanupSettingsPage @on-close="showSettingsModal = false" />
      </n-modal>

    </div>
  </n-layout>
</template>

<script setup>
import { ref, onMounted, computed, h } from 'vue';
import axios from 'axios';
import { 
  NLayout, NPageHeader, NDivider, NEmpty, NTag, NButton, NSpace, NIcon, 
  useMessage, NSpin, NAlert, NDataTable, NDropdown, useDialog, 
  NTooltip, NText, NModal
} from 'naive-ui';
import { 
  ScanCircleOutline as ScanIcon, 
  TrashBinOutline as DeleteIcon, 
  CheckmarkCircleOutline as KeepIcon,
  SettingsOutline as SettingsIcon
} from '@vicons/ionicons5';
import MediaCleanupSettingsPage from './settings/MediaCleanupSettingsPage.vue';

const props = defineProps({ taskStatus: { type: Object, required: true } });
const message = useMessage();
const dialog = useDialog();

const allTasks = ref([]);
const isLoading = ref(true);
const error = ref(null);
const selectedTasks = ref(new Set());
const showSettingsModal = ref(false);

const selectedTaskIds = computed({
  get: () => Array.from(selectedTasks.value),
  set: (keys) => { selectedTasks.value = new Set(keys); }
});

const isTaskRunning = (taskName) => props.taskStatus.is_running && props.taskStatus.current_action.includes(taskName);

const fetchData = async () => {
  isLoading.value = true;
  error.value = null;
  selectedTasks.value.clear();
  try {
    const response = await axios.get('/api/cleanup/tasks');
    allTasks.value = response.data;
  } catch (err) {
    error.value = err.response?.data?.error || '获取重复项列表失败。';
  } finally {
    isLoading.value = false;
  }
};

const triggerScan = () => {
  dialog.info({
    title: '确认开始扫描',
    content: '扫描会检查全库媒体的重复项问题，根据媒体库大小可能需要一些时间。确定要开始吗？',
    positiveText: '开始扫描',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await axios.post('/api/tasks/run', { 
          task_name: 'scan-cleanup-issues' 
        });
        message.success('扫描任务已提交到后台，请稍后查看任务状态。');
      } catch (err) {
        message.error(err.response?.data?.error || '提交扫描任务失败。');
      }
    }
  });
};

const batchActions = computed(() => [
  { label: `执行清理 (${selectedTasks.value.size}项)`, key: 'execute', props: { type: 'error' } },
  { label: `忽略 (${selectedTasks.value.size}项)`, key: 'ignore' },
  { label: `从列表移除 (${selectedTasks.value.size}项)`, key: 'delete' }
]);

const handleBatchAction = (key) => {
  const ids = Array.from(selectedTasks.value);
  if (ids.length === 0) return;

  if (key === 'execute') {
    dialog.warning({
      title: '高危操作确认',
      content: `确定要清理选中的 ${ids.length} 组重复项吗？此操作将永久删除多余的媒体文件，且不可恢复！`,
      positiveText: '我确定，执行清理！',
      negativeText: '取消',
      onPositiveClick: () => executeCleanup(ids)
    });
  } else if (key === 'ignore') {
    ignoreTasks(ids);
  } else if (key === 'delete') {
    deleteTasks(ids);
  }
};

const executeCleanup = async (ids) => {
  try {
    await axios.post('/api/cleanup/execute', { task_ids: ids });
    message.success('清理任务已提交到后台执行。');
    allTasks.value = allTasks.value.filter(task => !ids.includes(task.id));
    selectedTasks.value.clear();
  } catch (err) {
    message.error(err.response?.data?.error || '提交清理任务失败。');
  }
};

const ignoreTasks = async (ids) => {
  try {
    const response = await axios.post('/api/cleanup/ignore', { task_ids: ids });
    message.success(response.data.message);
    allTasks.value = allTasks.value.filter(task => !ids.includes(task.id));
    selectedTasks.value.clear();
  } catch (err) {
    message.error(err.response?.data?.error || '忽略任务失败。');
  }
};

const deleteTasks = async (ids) => {
  try {
    const response = await axios.post('/api/cleanup/delete', { task_ids: ids });
    message.success(response.data.message);
    allTasks.value = allTasks.value.filter(task => !ids.includes(task.id));
    selectedTasks.value.clear();
  } catch (err) {
    message.error(err.response?.data?.error || '删除任务失败。');
  }
};

const formatBytes = (bytes, decimals = 2) => {
  if (!bytes || bytes === 0) return '0 Bytes';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
};

// ★★★ 核心修改 1/3: 实现全选/取消全选的逻辑 ★★★
const handleSelectAll = (checked) => {
  if (checked) {
    selectedTasks.value = new Set(allTasks.value.map(task => task.id));
  } else {
    selectedTasks.value.clear();
  }
};

const columns = computed(() => [
  { 
    type: 'selection',
    // ★★★ 核心修改 2/3: 绑定全选逻辑到表头 ★★★
    onUpdateChecked: handleSelectAll,
    checked: selectedTasks.value.size === allTasks.value.length && allTasks.value.length > 0
  },
  { 
    title: '媒体项', 
    key: 'item_name',
    sorter: 'default',
    render(row) {
      return h('strong', null, row.item_name);
    }
  },
  {
    title: '版本详情',
    key: 'versions_info_json',
    render(row) {
      const versions = row.versions_info_json || [];
      const getVersionDisplayInfo = (v) => {
        const path_lower = (v.path || "").toLowerCase();
        const width = v.resolution_wh ? v.resolution_wh[0] : 0;
        let resolution = "Unknown";
        if (width >= 3840) resolution = "2160p";
        else if (width >= 1920) resolution = "1080p";
        else if (width >= 1280) resolution = "720p";
        const QUALITY_HIERARCHY = ["remux", "bluray", "blu-ray", "web-dl", "webdl", "webrip", "hdtv", "dvdrip"];
        let quality = "Unknown";
        for (const tag of QUALITY_HIERARCHY) {
            if (path_lower.includes(tag)) {
                quality = tag.replace('-', '').toUpperCase();
                break;
            }
        }
        let effect = "SDR";
        if (path_lower.includes("dovi") || path_lower.includes("dolby vision")) {
            effect = "DoVi";
        } else if (path_lower.includes("hdr10+")) {
            effect = "HDR10+";
        } else if (path_lower.includes("hdr")) {
            effect = "HDR";
        } else if (v.video_stream) {
            const range = (v.video_stream.VideoRangeType || "").toLowerCase();
            if (range.includes("dovi")) effect = "DoVi";
            else if (range.includes("hdr10+")) effect = "HDR10+";
            else if (range.includes("hdr")) effect = "HDR";
        }
        return { resolution, quality, effect };
      };
      const sortedVersions = [...versions].sort((a, b) => {
        if (a.id === row.best_version_id) return -1;
        if (b.id === row.best_version_id) return 1;
        return 0;
      });
      return h(NSpace, { vertical: true, size: 'small' }, {
        default: () => sortedVersions.map(v => {
          const isBest = v.id === row.best_version_id;
          const icon = isBest ? KeepIcon : DeleteIcon;
          const iconColor = isBest ? 'var(--n-success-color)' : 'var(--n-error-color)';
          const tooltipText = isBest ? '保留此版本' : '删除此版本';
          const displayInfo = getVersionDisplayInfo(v);
          return h(NTooltip, null, {
            trigger: () => h('div', { style: 'display: flex; align-items: center; gap: 8px;' }, [
              h(NIcon, { component: icon, color: iconColor, size: 16 }),
              h(NSpace, { size: 'small' }, {
                default: () => [
                  h(NTag, { size: 'small', bordered: false }, { default: () => displayInfo.resolution }),
                  h(NTag, { size: 'small', bordered: false, type: 'info' }, { default: () => displayInfo.quality }),
                  h(NTag, { size: 'small', bordered: false, type: 'warning' }, { default: () => displayInfo.effect }),
                  h(NTag, { size: 'small', bordered: false, type: 'success' }, { default: () => formatBytes(v.size) }),
                ]
              }),
              h(NText, { style: `font-weight: ${isBest ? 'bold' : 'normal'}; margin-left: 8px;` }, { 
                default: () => v.path
              })
            ]),
            default: () => tooltipText
          });
        })
      });
    }
  }
]);

// ★★★ 核心修改 3/3: 将 pagination 变成一个动态计算属性 ★★★
const pagination = computed(() => {
  if (allTasks.value.length > 20) {
    return {
      pageSize: 20,
      pageSizes: [20, 50, 100, { label: '全部', value: allTasks.value.length }],
      showSizePicker: true,
    };
  }
  // 如果总数小于等于20，就不显示分页器
  return false;
});

onMounted(fetchData);
</script>

<style scoped>
.center-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: calc(100vh - 300px);
}
</style>
<!-- src/components/LogViewer.vue -->
<template>
  <n-drawer
    :show="show"
    :width="900"
    @update:show="$emit('update:show', $event)"
    placement="right"
    resizable
  >
    <n-drawer-content title="历史日志查看器" closable>
      <n-space vertical>
        <!-- 搜索栏 -->
        <n-input-group>
          <n-input
            v-model:value="searchQuery"
            placeholder="在所有日志文件中搜索..."
            clearable
            @keyup.enter="executeSearch"
            :disabled="isLoading"
          />
          <n-button type="primary" @click="executeSearch" :loading="isSearching">
            搜索
          </n-button>
        </n-input-group>

        <!-- 搜索模式切换 -->
        <n-radio-group v-model:value="searchMode" name="search-mode-radio">
          <n-radio-button value="filter" :disabled="isLoading">
            筛选模式 (仅显示匹配行)
          </n-radio-button>
          <n-radio-button value="context" :disabled="isLoading">
            定位模式 (显示完整处理过程)
          </n-radio-button>
        </n-radio-group>

        <n-divider style="margin-top: 5px; margin-bottom: 5px;" />

        <!-- 结果展示区 -->
        <n-spin :show="isLoading">
          <!-- 模式A: 搜索结果视图 -->
          <div v-if="isSearchMode">
            <n-button @click="clearSearch" size="small" style="margin-bottom: 10px;">
              <template #icon><n-icon :component="ArrowBackOutline" /></template>
              返回文件浏览
            </n-button>
            <n-log
              v-if="hasSearchResults"
              :log="formattedLogResults"
              :rows="35"
              style="font-size: 12px; line-height: 1.6;"
            />
            <n-empty v-else description="未找到匹配的日志记录。" style="margin-top: 50px;" />
          </div>

          <!-- 模式B: 文件浏览视图 (默认) -->
          <div v-else>
            <n-select
              v-model:value="selectedFile"
              placeholder="请选择一个日志文件"
              :options="fileOptions"
              :loading="isLoadingFiles"
              @update:value="fetchLogContent"
            />
            <n-log
              v-if="logContent"
              :log="logContent"
              trim
              :rows="38"
              style="font-size: 12px; line-height: 1.5; margin-top: 10px;"
            />
            <n-empty v-else description="无数据" style="margin-top: 50px;" />
          </div>
          <template #description>{{ loadingText }}</template>
        </n-spin>
      </n-space>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup>
import { ref, watch, computed } from 'vue';
import axios from 'axios';
import { 
  useMessage, NDrawer, NDrawerContent, NSelect, NSpace, NSpin, NLog, 
  NInput, NInputGroup, NButton, NDivider, NEmpty, NIcon,
  NRadioGroup, NRadioButton
} from 'naive-ui';
import { ArrowBackOutline } from '@vicons/ionicons5';

// --- Props, Emits ---
const props = defineProps({ show: { type: Boolean, default: false } });
const emit = defineEmits(['update:show']);

// --- Refs ---
const message = useMessage();
const isLoadingFiles = ref(false);
const isLoadingContent = ref(false);
const isSearching = ref(false);
const logFiles = ref([]);
const selectedFile = ref(null);
const logContent = ref(''); // 初始为空，避免显示“无数据”
const searchQuery = ref('');
const searchResults = ref([]);
const isSearchMode = ref(false);
const searchMode = ref('context');

// --- Computed ---
const isLoading = computed(() => isLoadingFiles.value || isLoadingContent.value || isSearching.value);
const hasSearchResults = computed(() => searchResults.value.length > 0);
const fileOptions = computed(() => logFiles.value.map(file => ({ label: file, value: file })));
const loadingText = computed(() => {
  if (isLoadingFiles.value) return '正在获取文件列表...';
  if (isLoadingContent.value) return '正在加载日志内容...';
  if (isSearching.value) return `正在以 [${searchMode.value === 'context' ? '定位' : '筛选'}] 模式搜索...`;
  return '';
});
const formattedLogResults = computed(() => {
  if (!hasSearchResults.value) return '';
  if (searchMode.value === 'context') {
    const blocks = searchResults.value.map(block => `--- [ Context found in ${block.file} ] ---\n${block.lines.join('\n')}`);
    return `以“定位”模式找到 ${blocks.length} 个完整处理过程:\n\n` + blocks.join('\n\n========================================================\n\n');
  } else {
    let lastFile = '';
    const lines = searchResults.value.map(result => `${result.file !== lastFile ? `\n--- [ ${lastFile=result.file} ] ---\n` : ''}${result.content}`);
    return `以“筛选”模式找到 ${searchResults.value.length} 条结果:` + lines.join('\n');
  }
});

// --- Methods ---

const fetchLogFiles = async () => {
  isLoadingFiles.value = true;
  try {
    const response = await axios.get('/api/logs/list');
    logFiles.value = response.data;
    // ★★★ 关键修复：确保在文件浏览模式下，自动加载第一个文件 ★★★
    if (!isSearchMode.value && logFiles.value.length > 0) {
      // 只有当没有文件被选中时，才自动选择第一个
      if (!selectedFile.value) {
        selectedFile.value = logFiles.value[0];
        await fetchLogContent(selectedFile.value);
      }
    } else if (logFiles.value.length === 0) {
      logContent.value = ''; // 如果没文件，清空内容区
    }
  } catch (error) {
    message.error('获取日志文件列表失败！');
  } finally {
    isLoadingFiles.value = false;
  }
};

const fetchLogContent = async (filename) => {
  if (!filename) return;
  isLoadingContent.value = true;
  logContent.value = `正在加载 ${filename}...`;
  try {
    const response = await axios.get('/api/logs/view', { params: { filename } });
    logContent.value = response.data || '（文件为空）';
  } catch (error) {
    message.error(`加载日志 ${filename} 失败！`);
    logContent.value = `加载文件失败: ${error.response?.data || '未知错误'}`;
  } finally {
    isLoadingContent.value = false;
  }
};

const executeSearch = async () => {
  if (!searchQuery.value.trim()) {
    message.warning('请输入搜索关键词。');
    return;
  }
  isSearching.value = true;
  isSearchMode.value = true;
  searchResults.value = [];
  const endpoint = searchMode.value === 'context' ? '/api/logs/search_context' : '/api/logs/search';
  try {
    const response = await axios.get(endpoint, { params: { q: searchQuery.value } });
    searchResults.value = response.data;
  } catch (error) {
    message.error(error.response?.data?.error || '搜索失败！');
  } finally {
    isSearching.value = false;
  }
};

const clearSearch = () => {
  isSearchMode.value = false;
  searchQuery.value = '';
  searchResults.value = [];
  // ★★★ 关键修复：返回浏览模式时，如果内容区是空的，则重新加载当前选中的文件 ★★★
  if (selectedFile.value && !logContent.value) {
    fetchLogContent(selectedFile.value);
  }
};

// --- Watcher ---
watch(() => props.show, (newVal) => {
  if (newVal) {
    fetchLogFiles();
  } else {
    // 关闭时重置所有状态
    clearSearch();
    selectedFile.value = null;
    logFiles.value = [];
    logContent.value = '';
  }
});
</script>
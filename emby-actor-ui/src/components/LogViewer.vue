<!-- src/components/LogViewer.vue -->
<template>
  <n-drawer
    :show="show"
    :width="800"
    @update:show="$emit('update:show', $event)"
    placement="right"
    resizable
  >
    <n-drawer-content title="历史日志查看器" closable>
      <n-space vertical>
        <!-- ★★★ 1. 新增搜索栏 ★★★ -->
        <n-input-group>
          <n-input
            v-model:value="searchQuery"
            placeholder="在所有日志文件中搜索 (不区分大小写)"
            clearable
            @keyup.enter="executeSearch"
            :disabled="isLoading"
          />
          <n-button type="primary" @click="executeSearch" :loading="isLoading">
            搜索
          </n-button>
        </n-input-group>

        <!-- 分隔线，用于区分搜索和浏览 -->
        <n-divider style="margin-top: 5px; margin-bottom: 5px;" />

        <!-- ★★★ 2. 根据模式显示不同内容 ★★★ -->
        <n-spin :show="isLoading">
          <!-- 模式A: 搜索结果视图 -->
          <div v-if="isSearchMode">
            <n-button @click="clearSearch" size="small" style="margin-bottom: 10px;">
              <template #icon><n-icon :component="ArrowBackOutline" /></template>
              返回文件浏览
            </n-button>
            <n-log
              v-if="searchResults.length > 0"
              :log="formattedSearchResults"
              :rows="38"
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
              :log="logContent"
              trim
              :rows="38"
              style="font-size: 12px; line-height: 1.5; margin-top: 10px;"
            />
          </div>
          <template #description>
            {{ loadingText }}
          </template>
        </n-spin>
      </n-space>
    </n-drawer-content>
  </n-drawer>
</template>

<script setup>
import { ref, watch, computed } from 'vue';
import axios from 'axios';
import { 
  useMessage, NDrawer, NDrawerContent, NSelect, NSpace, NSpin, NLog, NCard,
  NInput, NInputGroup, NButton, NDivider, NEmpty, NIcon
} from 'naive-ui';
import { ArrowBackOutline } from '@vicons/ionicons5';

// --- Props and Emits (保持不变) ---
const props = defineProps({ show: { type: Boolean, default: false } });
const emit = defineEmits(['update:show']);

// --- 核心状态 (Refs) ---
const message = useMessage();
const isLoadingFiles = ref(false);
const isLoadingContent = ref(false);
const isSearching = ref(false);

const logFiles = ref([]);
const selectedFile = ref(null);
const logContent = ref('请从上方选择一个日志文件进行查看。');

const searchQuery = ref('');
const searchResults = ref([]);
const isSearchMode = ref(false); // 控制显示哪个视图

// --- 计算属性 (Computed) ---
const isLoading = computed(() => isLoadingFiles.value || isLoadingContent.value || isSearching.value);
const loadingText = computed(() => {
  if (isLoadingFiles.value) return '正在获取文件列表...';
  if (isLoadingContent.value) return '正在加载日志内容...';
  if (isSearching.value) return `正在搜索 "${searchQuery.value}"...`;
  return '';
});

const fileOptions = computed(() => 
  logFiles.value.map(file => ({ label: file, value: file }))
);

// ★★★ 3. 新增：格式化搜索结果，并高亮关键词 ★★★
const formattedSearchResults = computed(() => {
  if (searchResults.value.length === 0) return '';
  const query = searchQuery.value.toLowerCase();
  let lastFile = '';
  
  const lines = searchResults.value.map(result => {
    let header = '';
    if (result.file !== lastFile) {
      header = `\n--- [ ${result.file} ] ---\n`;
      lastFile = result.file;
    }
    // 简单的关键词高亮（注意：这不是安全的 HTML 注入，但对于日志显示是可接受的）
    // Naive UI 的 n-log 不支持 v-html，所以我们用特殊字符代替高亮
    const content = result.content.replace(new RegExp(query, 'gi'), (match) => `🌟${match}🌟`);
    return `${header}${content}`;
  });

  return `搜索 "${searchQuery.value}" 找到 ${searchResults.value.length} 条结果:` + lines.join('\n');
});


// --- 方法 (Methods) ---

// 获取文件列表 (保持基本不变)
const fetchLogFiles = async () => {
  isLoadingFiles.value = true;
  try {
    const response = await axios.get('/api/logs/list');
    logFiles.value = response.data;
    if (logFiles.value.length > 0 && !selectedFile.value) {
      selectedFile.value = logFiles.value[0];
      await fetchLogContent(selectedFile.value);
    }
  } catch (error) {
    message.error('获取日志文件列表失败！');
  } finally {
    isLoadingFiles.value = false;
  }
};

// 获取单个文件内容 (保持不变)
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

// ★★★ 4. 新增：执行搜索的方法 ★★★
const executeSearch = async () => {
  if (!searchQuery.value.trim()) {
    message.warning('请输入搜索关键词。');
    return;
  }
  isSearching.value = true;
  isSearchMode.value = true; // 切换到搜索结果视图
  searchResults.value = [];
  try {
    const response = await axios.get('/api/logs/search', {
      params: { q: searchQuery.value },
    });
    searchResults.value = response.data;
  } catch (error) {
    message.error(error.response?.data?.error || '搜索失败！');
  } finally {
    isSearching.value = false;
  }
};

// ★★★ 5. 新增：清除搜索并返回文件浏览模式 ★★★
const clearSearch = () => {
  isSearchMode.value = false;
  searchQuery.value = '';
  searchResults.value = [];
  // 如果之前没有加载过文件内容，可以重新加载一下
  if (!logContent.value.startsWith('请从上方选择')) {
    // 内容已在，无需操作
  } else if (selectedFile.value) {
    fetchLogContent(selectedFile.value);
  }
};

// --- 监听器 (Watch) ---
watch(() => props.show, (newVal) => {
  if (newVal) {
    // 打开时，只获取文件列表，并加载默认文件
    fetchLogFiles();
  } else {
    // 关闭时，完全重置状态
    clearSearch(); // 清除搜索状态
    selectedFile.value = null;
    logFiles.value = [];
    logContent.value = '请从上方选择一个日志文件进行查看。';
  }
});
</script>
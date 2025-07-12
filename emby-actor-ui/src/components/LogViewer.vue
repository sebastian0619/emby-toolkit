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
          <n-button type="primary" @click="executeSearch" :loading="isLoading">
            搜索
          </n-button>
        </n-input-group>

        <!-- ★★★ 1. 新增：搜索模式切换 ★★★ -->
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
              :log="logContent"
              trim
              :rows="38"
              style="font-size: 12px; line-height: 1.5; margin-top: 10px;"
            />
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

// --- Props, Emits, and Core Refs (大部分保持不变) ---
const props = defineProps({ show: { type: Boolean, default: false } });
const emit = defineEmits(['update:show']);
const message = useMessage();
const isLoadingFiles = ref(false);
const isLoadingContent = ref(false);
const isSearching = ref(false);
const logFiles = ref([]);
const selectedFile = ref(null);
const logContent = ref('请从上方选择一个日志文件进行查看。');
const searchQuery = ref('');
const searchResults = ref([]); // 将同时用于两种模式的结果
const isSearchMode = ref(false);

// ★★★ 2. 新增：控制搜索模式的 Ref ★★★
const searchMode = ref('context'); // 默认使用更强大的“定位模式”

// --- 计算属性 (Computed) ---
const isLoading = computed(() => isLoadingFiles.value || isLoadingContent.value || isSearching.value);
const hasSearchResults = computed(() => searchResults.value.length > 0);
const loadingText = computed(() => {
  if (isSearching.value) return `正在以 [${searchMode.value === 'context' ? '定位' : '筛选'}] 模式搜索 "${searchQuery.value}"...`;
  return '';
});
const fileOptions = computed(() => logFiles.value.map(file => ({ label: file, value: file })));

// ★★★ 3. 升级：统一的结果格式化器 ★★★
const formattedLogResults = computed(() => {
  if (!hasSearchResults.value) return '';

  if (searchMode.value === 'context') {
    // 为“定位模式”格式化结果
    const blocks = searchResults.value.map(block => {
      const header = `--- [ Context found in ${block.file} ] ---`;
      const content = block.lines.join('\n');
      return `${header}\n${content}`;
    });
    return `以“定位”模式找到 ${blocks.length} 个完整处理过程:\n\n` + blocks.join('\n\n========================================================\n\n');
  } else {
    // 为“筛选模式”格式化结果
    let lastFile = '';
    const lines = searchResults.value.map(result => {
      let header = '';
      if (result.file !== lastFile) {
        header = `\n--- [ ${result.file} ] ---\n`;
        lastFile = result.file;
      }
      return `${header}${result.content}`;
    });
    return `以“筛选”模式找到 ${searchResults.value.length} 条结果:` + lines.join('\n');
  }
});

// --- 方法 (Methods) ---

// ★★★ 4. 升级：执行搜索的方法，根据模式调用不同 API ★★★
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
    const response = await axios.get(endpoint, {
      params: { q: searchQuery.value },
    });
    searchResults.value = response.data;
  } catch (error) {
    message.error(error.response?.data?.error || '搜索失败！');
  } finally {
    isSearching.value = false;
  }
};

// 其他方法 (fetchLogFiles, fetchLogContent, clearSearch) 保持不变
const fetchLogFiles = async () => { /* ... 保持原样 ... */ };
const fetchLogContent = async (filename) => { /* ... 保持原样 ... */ };
const clearSearch = () => {
  isSearchMode.value = false;
  searchQuery.value = '';
  searchResults.value = [];
};

// --- 监听器 (Watch) ---
watch(() => props.show, (newVal) => {
  if (newVal) {
    fetchLogFiles();
  } else {
    clearSearch();
    selectedFile.value = null;
    logFiles.value = [];
    logContent.value = '请从上方选择一个日志文件进行查看。';
  }
});
</script>
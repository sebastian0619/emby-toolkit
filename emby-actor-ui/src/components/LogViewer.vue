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
            
            <!-- ★★★ [修改] 将级别class绑定到整行div上 ★★★ -->
            <div v-if="hasSearchResults" class="log-viewer-container">
              <div 
                v-for="(line, index) in parsedLogResults" 
                :key="index" 
                class="log-line"
                :class="line.type === 'log' ? line.level.toLowerCase() : 'raw'"
              >
                <template v-if="line.type === 'log'">
                  <span class="timestamp">{{ line.timestamp }}</span>
                  <span class="level">{{ line.level }}</span>
                  <span class="message">{{ line.message }}</span>
                </template>
                <template v-else>
                  {{ line.content }}
                </template>
              </div>
            </div>
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

            <!-- ★★★ [修改] 将级别class绑定到整行div上 ★★★ -->
            <div v-if="logContent" class="log-viewer-container" style="margin-top: 10px;">
              <div 
                v-for="(line, index) in parsedLogContent" 
                :key="index" 
                class="log-line"
                :class="line.type === 'log' ? line.level.toLowerCase() : 'raw'"
              >
                <template v-if="line.type === 'log'">
                  <span class="timestamp">{{ line.timestamp }}</span>
                  <span class="level">{{ line.level }}</span>
                  <span class="message">{{ line.message }}</span>
                </template>
                <template v-else>
                  {{ line.content }}
                </template>
              </div>
            </div>
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
  useMessage, NDrawer, NDrawerContent, NSelect, NSpace, NSpin, 
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
const logContent = ref('');
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

// 日志行解析函数 (无需改动)
const parseLogLine = (line) => {
  const match = line.match(/^(\d{4}-\d{2}-\d{2}\s(\d{2}:\d{2}:\d{2})),\d+\s-\s.+?\s-\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s-\s(.*)$/);
  if (match) {
    return {
      type: 'log',
      timestamp: match[2],
      level: match[3],
      message: match[4].trim(),
    };
  }
  return { type: 'raw', content: line };
};

// 计算属性，用于解析日志内容 (无需改动)
const parsedLogContent = computed(() => {
  if (!logContent.value) return [];
  return logContent.value.split('\n').map(parseLogLine);
});

// 计算属性，用于解析搜索结果 (无需改动)
const parsedLogResults = computed(() => {
  if (!hasSearchResults.value) return [];

  // 最终要渲染的行数组，每一项都是一个独立的字符串
  const finalLines = [];

  if (searchMode.value === 'context') {
    // --- “定位”模式重构 ---
    finalLines.push(`以“定位”模式找到 ${searchResults.value.length} 个完整处理过程:`);
    
    searchResults.value.forEach((block, index) => {
      // 为每个块添加一个空行作为间距
      finalLines.push(''); 
      
      const datePart = block.date && block.date.includes(' ') ? block.date.split(' ')[0] : block.date;
      finalLines.push(`--- [ 记录在 ${block.file} 于 ${datePart} ] ---`);
      
      // 将块内的所有行逐一添加到最终数组中
      block.lines.forEach(line => finalLines.push(line));
      
      // 在块与块之间添加分隔符，但最后一个块后面不加
      if (index < searchResults.value.length - 1) {
        finalLines.push('');
        finalLines.push('========================================================');
      }
    });

  } else {
    // --- “筛选”模式重构 (核心修复) ---
    finalLines.push(`以“筛选”模式找到 ${searchResults.value.length} 条结果:`);

    let lastFile = '';
    let lastDatePart = '';

    searchResults.value.forEach(result => {
      const currentDatePart = result.date ? result.date.split(' ')[0] : '';
      
      // 当文件名或日期变化时，插入一个新的分组头
      if (result.file !== lastFile || currentDatePart !== lastDatePart) {
        // 在新的分组头前加一个空行，增加可读性（但不在最开始加）
        if (finalLines.length > 1) {
            finalLines.push('');
        }
        
        finalLines.push(`--- [ 记录在 ${result.file} 于 ${currentDatePart || '未知'} ] ---`);
        
        lastFile = result.file;
        lastDatePart = currentDatePart;
      }
      
      // 直接将单条日志内容作为独立一项推入数组
      finalLines.push(result.content);
    });
  }
  
  // 最后，将这个结构清晰的行数组交给 parseLogLine 进行格式化
  return finalLines.map(parseLogLine);
});


// --- Methods --- (无需改动)
const fetchLogFiles = async () => {
  isLoadingFiles.value = true;
  try {
    const response = await axios.get('/api/logs/list');
    logFiles.value = response.data;
    if (!isSearchMode.value && logFiles.value.length > 0) {
      if (!selectedFile.value) {
        selectedFile.value = logFiles.value[0];
        await fetchLogContent(selectedFile.value);
      }
    } else if (logFiles.value.length === 0) {
      logContent.value = '';
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
  if (selectedFile.value && !logContent.value) {
    fetchLogContent(selectedFile.value);
  }
};

// --- Watcher --- (无需改动)
watch(() => props.show, (newVal) => {
  if (newVal) {
    fetchLogFiles();
  } else {
    clearSearch();
    selectedFile.value = null;
    logFiles.value = [];
    logContent.value = '';
  }
});
</script>

<!-- ★★★ [修改] 将颜色样式应用到整行 ★★★ -->
<style scoped>
.log-viewer-container {
  background-color: #282c34;
  font-family: 'Courier New', Courier, monospace;
  font-size: 13px;
  padding: 10px 15px;
  border-radius: 6px;
  max-height: calc(100vh - 250px);
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.log-line {
  line-height: 1.6;
  padding: 1px 0;
  /* 默认文字颜色，会被下面的具体级别覆盖 */
  color: #abb2bf; 
}

/* 不同级别的行颜色 */
.log-line.info { color: #98c379; }
.log-line.warning { color: #e5c07b; }
.log-line.error,
.log-line.critical { color: #e06c75; }
.log-line.debug { color: #56b6c2; }

/* 分隔符等原始行的样式 */
.log-line.raw {
  color: #95a5a6;
  font-style: italic;
}

/* 时间戳颜色保持独立，不受行颜色影响 */
.timestamp {
  color: #61afef;
  margin-right: 1em;
}

.level {
  font-weight: bold;
  margin-right: 1em;
  text-transform: uppercase;
}
</style>
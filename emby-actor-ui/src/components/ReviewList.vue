<!-- ReviewList.vue -->
<template>
  <n-card title="媒体库浏览器" :bordered="false" size="large">
    <n-alert 
      v-if="taskStatus.is_running" 
      title="后台任务运行中" 
      type="warning" 
      style="margin-bottom: 15px;"
    >
      后台任务正在运行，此时“手动处理”等操作可能会失败。
    </n-alert>
    <n-spin :show="loading">
      <div v-if="error" class="error-message">
        <n-alert title="加载错误" type="error">{{ error }}</n-alert>
      </div>
      <div v-else>
        <!-- 搜索框 -->
        <n-input
          v-model:value="searchQuery"
          placeholder="输入媒体名称搜索整个 Emby 库..."
          clearable
          @keyup.enter="handleSearch"
          @clear="handleSearch"
          style="margin-bottom: 15px; max-width: 400px;"
        >
          <template #suffix>
            <n-icon :component="SearchIcon" @click="handleSearch" style="cursor: pointer;" />
          </template>
        </n-input>

        <!-- 表格 -->
        <!-- ✨✨✨ 确保这里使用 tableData ✨✨✨ -->
        <n-data-table
          v-if="tableData.length > 0"
          :columns="columns"
          :data="tableData"
          :pagination="paginationProps"
          :bordered="false"
          striped
          size="small"
          :row-key="row => row.item_id"
          :loading="loadingAction[currentRowId]"
          remote 
        />
        <!-- ✨✨✨ 确保这里也使用 tableData ✨✨✨ -->
        <n-empty 
          v-else-if="!loading && tableData.length === 0" 
          :description="isShowingSearchResults ? '在 Emby 库中未找到匹配项。' : '没有需要手动处理的媒体项。'" 
          style="margin-top: 20px;" 
        />
        <!-- ✨✨✨ 确保这里也使用 tableData ✨✨✨ -->
        <p v-if="loading && tableData.length === 0">正在加载...</p>
      </div>
    </n-spin>
  </n-card>
</template>

<script setup>
import { useRouter } from 'vue-router';
import { ref, onMounted, computed, h } from 'vue';
import axios from 'axios';
import {
    NCard, NSpin, NAlert, NText, NDataTable, NButton, NSpace, NPopconfirm, NEmpty, NInput, NIcon,
    useMessage
} from 'naive-ui';
import { SearchOutline as SearchIcon, PlayForwardOutline as ReprocessIcon, CheckmarkCircleOutline as MarkDoneIcon } from '@vicons/ionicons5';

// --- 基础设置 ---
const router = useRouter();
const message = useMessage();

// --- 状态变量 ---
const tableData = ref([]);
const loading = ref(true);
const error = ref(null);
const totalItems = ref(0);
const currentPage = ref(1);
const itemsPerPage = ref(15);
const searchQuery = ref('');
const loadingAction = ref({});
const currentRowId = ref(null);
const isShowingSearchResults = ref(false);

// --- 表格列定义 ---
const formatDate = (timestamp) => {
  if (!timestamp) return 'N/A';
  try {
    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return '无效日期';
    return date.toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false
    });
  } catch (e) {
    return '日期格式化错误';
  }
};

const goToEditPage = (row) => {
  if (row && row.item_id) {
    router.push({ name: 'MediaEditPage', params: { itemId: row.item_id } });
  } else {
    message.error("无效的媒体项，无法跳转到编辑页面！");
  }
};

const handleMarkAsProcessed = async (row) => {
  currentRowId.value = row.item_id;
  loadingAction.value[row.item_id] = true;
  try {
    await axios.post(`/api/actions/mark_item_processed/${row.item_id}`);
    message.success(`项目 "${row.item_name}" 已标记为已处理。`);
    // 刷新列表
    fetchReviewItems();
  } catch (err) {
    console.error("标记为已处理失败:", err);
    message.error(`标记项目 "${row.item_name}" 为已处理失败: ${err.response?.data?.error || err.message}`);
  } finally {
    loadingAction.value[row.item_id] = false;
    currentRowId.value = null;
  }
};

const columns = computed(() => [
  {
    title: '媒体名称 (ID)',
    key: 'item_name',
    resizable: true,
    render(row) {
      return h('div', null, [
        h(NText, { strong: true }, { default: () => row.item_name || '未知名称' }),
        h(NText, { depth: 3, style: 'font-size: 0.8em; display: block; margin-top: 2px;' }, { default: () => `(ID: ${row.item_id || 'N/A'})` })
      ]);
    }
  },
  { title: '类型', key: 'item_type', width: 80, resizable: true },
  {
    title: '记录时间',
    key: 'failed_at',
    width: 170,
    resizable: true,
    render(row) { return formatDate(row.failed_at); }
  },
  { title: '原因', key: 'error_message', resizable: true, ellipsis: { tooltip: true } },
  {
    title: '评分',
    key: 'score',
    width: 80,
    resizable: true,
    render(row) {
      return row.score !== null && row.score !== undefined ? row.score.toFixed(1) : 'N/A';
    }
  },
  {
    title: '操作',
    key: 'actions',
    width: 220,
    align: 'center',
    fixed: 'right',
    render(row) {
      return h(NSpace, { justify: 'center' }, {
        default: () => [
          h(NButton,
            {
              size: 'small',
              type: 'primary',
              onClick: () => goToEditPage(row)
            },
            { default: () => '手动编辑' }
          ),
          // 只有在非搜索结果时，才显示“标记已处理”按钮
          !isShowingSearchResults.value ? h(NPopconfirm,
            {
              onPositiveClick: () => handleMarkAsProcessed(row),
            },
            {
              trigger: () => h(NButton,
                {
                  size: 'small',
                  type: 'success',
                  ghost: true,
                  loading: loadingAction.value[row.item_id] && currentRowId.value === row.item_id,
                  disabled: loadingAction.value[row.item_id]
                },
                {
                  icon: () => h(NIcon, { component: MarkDoneIcon }),
                  default: () => '标记已处理'
                }
              ),
              default: () => `确定要将 "${row.item_name}" 标记为已处理吗？`
            }
          ) : null,
        ]
      });
    }
  }
]);

// --- 分页逻辑 ---
const paginationProps = computed(() => ({
    disabled: isShowingSearchResults.value,
    page: currentPage.value,
    pageSize: itemsPerPage.value,
    itemCount: totalItems.value,
    showSizePicker: true,
    pageSizes: [10, 15, 20, 30, 50, 100],
    onChange: (page) => {
        currentPage.value = page;
        if (!isShowingSearchResults.value) {
            fetchReviewItems();
        }
    },
    onUpdatePageSize: (pageSize) => {
        itemsPerPage.value = pageSize;
        currentPage.value = 1;
        if (!isShowingSearchResults.value) {
            fetchReviewItems();
        }
    }
}));

// --- 数据获取逻辑 ---
const fetchReviewItems = async () => {
  loading.value = true;
  error.value = null;
  isShowingSearchResults.value = false;
  try {
    const response = await axios.get(`/api/review_items`, {
        params: {
            page: currentPage.value,
            per_page: itemsPerPage.value,
        }
    });
    tableData.value = response.data.items;
    totalItems.value = response.data.total_items;
  } catch (err) {
    handleFetchError(err, "加载待处理列表失败。");
  } finally {
    loading.value = false;
  }
};

const searchEmbyLibrary = async () => {
  loading.value = true;
  error.value = null;
  isShowingSearchResults.value = true;
  try {
    const response = await axios.get(`/api/search_emby_library`, {
        params: { query: searchQuery.value }
    });
    tableData.value = response.data.items;
    totalItems.value = response.data.total_items;
  } catch (err) {
    handleFetchError(err, "搜索 Emby 媒体库失败。");
  } finally {
    loading.value = false;
  }
};

const handleFetchError = (err, defaultMessage) => {
    console.error(defaultMessage, err);
    error.value = defaultMessage + (err.response?.data?.error || err.message);
    message.error(error.value);
};

// --- 搜索处理 ---
const handleSearch = () => {
  if (searchQuery.value.trim()) {
    currentPage.value = 1;
    searchEmbyLibrary();
  } else {
    currentPage.value = 1;
    fetchReviewItems();
  }
};

// --- 生命周期钩子 ---
defineProps({
  taskStatus: {
    type: Object,
    required: true
  }
});

onMounted(() => {
  fetchReviewItems();
});
</script>

<style scoped>
.error-message { margin-bottom: 15px; }
</style>
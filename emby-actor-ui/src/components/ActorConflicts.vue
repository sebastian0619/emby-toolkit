<template>
  <div class="actor-conflicts-page">
    <n-page-header>
      <template #title>
        演员冲突裁决中心
      </template>
    </n-page-header>
    <n-divider />

    <n-card :bordered="false">
      <!-- 顶部操作区 -->
      <n-space align="center" justify="space-between" style="margin-bottom: 20px;">
        <n-input
          v-model:value="searchQuery"
          placeholder="输入演员名搜索冲突案件..."
          clearable
          @keyup.enter="handleSearch"
          @clear="handleSearch"
          style="max-width: 400px;"
        >
          <template #suffix>
            <n-icon :component="SearchIcon" @click="handleSearch" style="cursor: pointer;" />
          </template>
        </n-input>
        
        <n-button @click="startDuplicateScan" :loading="isScanning">
          <template #icon><n-icon :component="ScanIcon" /></template>
          扫描重复演员
        </n-button>
      </n-space>

      <!-- 数据表格 -->
      <n-spin :show="isLoading">
        <div v-if="error" class="center-container">
          <n-alert title="加载错误" type="error">{{ error }}</n-alert>
        </div>
        <div v-else>
          <n-data-table
            v-if="conflicts.length > 0"
            :columns="columns"
            :data="conflicts"
            :pagination="paginationProps"
            :bordered="false"
            :single-line="false"
            striped
            size="small"
            :row-key="row => row.conflict_id"
            remote
          />
          <n-empty 
            v-else-if="!isLoading && conflicts.length === 0" 
            description="天下太平！没有需要您裁决的演员冲突案件。" 
            style="margin-top: 50px; margin-bottom: 30px;" 
            size="huge"
          />
        </div>
      </n-spin>
    </n-card>

    <!-- 裁决模态框 -->
    <n-modal v-model:show="showModal" preset="card" title="冲突裁决" style="width: 80%; max-width: 900px;">
      <div v-if="editableConflict">
        <n-alert :title="`案件 #${editableConflict.conflict_id}`" type="info" style="margin-bottom: 20px;">
          请仔细比对下方两位演员的信息，然后选择一个最合适的操作。
        </n-alert>
        <n-grid cols="1 m:2" :x-gap="20" responsive="screen">
          <!-- “原告”信息 -->
          <n-gi>
            <n-card :title="`新记录 - ${getConflictTypeName(editableConflict.conflict_type).text}`">
              <template #header-extra><n-tag type="warning">尝试关联者</n-tag></template>
              <n-space vertical align="center">
                <a :href="`https://www.themoviedb.org/person/${editableConflict.new_tmdb_id}`" target="_blank" title="在TMDb中查看">
                  <n-image :src="editableConflict.new_actor_image_url" class="conflict-avatar" />
                </a>
                <n-input v-model:value="editableConflict.new_actor_name" placeholder="演员名" style="text-align: center;" />
                <n-text depth="3">TMDb ID: {{ editableConflict.new_tmdb_id }}</n-text>
              </n-space>
            </n-card>
          </n-gi>
          <!-- “被告”信息 -->
          <n-gi>
            <n-card title="旧记录">
              <template #header-extra><n-tag type="success">当前占用者</n-tag></template>
              <n-space vertical align="center">
                <a :href="`https://www.themoviedb.org/person/${editableConflict.existing_tmdb_id}`" target="_blank" title="在TMDb中查看">
                  <n-image :src="editableConflict.existing_actor_image_url" class="conflict-avatar" />
                </a>
                <n-input v-model:value="editableConflict.existing_actor_name" placeholder="演员名" style="text-align: center;" />
                <n-text depth="3">TMDb ID: {{ editableConflict.existing_tmdb_id }}</n-text>
              </n-space>
            </n-card>
            </n-gi>
        </n-grid>
        
        <n-divider title-placement="center">裁决操作</n-divider>
        
        <n-space justify="center" :size="24">
          <n-popconfirm @positive-click="() => resolve('merge_new_to_existing')">
            <template #trigger>
              <n-button type="primary" ghost>是同一个人，合并</n-button>
            </template>
            确定要将“原告”合并到“被告”吗？这将把原告设为被告的别名。
          </n-popconfirm>

          <n-popconfirm @positive-click="() => resolve('unbind_existing')" v-if="editableConflict.conflict_type !== 'POTENTIAL_DUPLICATE'">
            <template #trigger>
              <n-button type="error">不是同一个人，解绑占用</n-button>
            </template>
            确定要将“被告”的关联解除，让“原告”可以关联吗？
          </n-popconfirm>

          <n-popconfirm @positive-click="() => resolve('ignore')">
            <template #trigger>
              <n-button type="tertiary">确认是两人，忽略冲突</n-button>
            </template>
            确定要忽略这个冲突吗？它将不再显示。
          </n-popconfirm>
        </n-space>
      </div>
    </n-modal>
  </div>
</template>

<script setup>
import { ref, onMounted, computed, h, watch } from 'vue';
import axios from 'axios';
import { NPageHeader, NDivider, NCard, NSpace, NInput, NIcon, NButton, NSpin, NAlert, NDataTable, NEmpty, NTag, NModal, NGrid, NGi, NImage, NH3, NText, NPopconfirm, useMessage } from 'naive-ui';
import { RefreshOutline as RefreshIcon, SearchOutline as SearchIcon, ScanCircleOutline as ScanIcon } from '@vicons/ionicons5';

const message = useMessage();

// --- State Refs ---
const isLoading = ref(true);
const error = ref(null);
const conflicts = ref([]);
const searchQuery = ref('');
const isScanning = ref(false);

// Pagination State
const pagination = ref({ page: 1, pageSize: 15, itemCount: 0 });

// Modal State
const showModal = ref(false);
const currentConflict = ref(null);
const editableConflict = ref(null); // 用于编辑的副本

// --- Computed Properties ---
const paginationProps = computed(() => ({
  page: pagination.value.page,
  pageSize: pagination.value.pageSize,
  itemCount: pagination.value.itemCount,
  onUpdatePage: (page) => {
    pagination.value.page = page;
    fetchConflicts();
  },
  onUpdatePageSize: (pageSize) => {
    pagination.value.pageSize = pageSize;
    pagination.value.page = 1;
    fetchConflicts();
  },
  showSizePicker: true,
  pageSizes: [10, 15, 20, 50]
}));

const columns = computed(() => [
  { title: '案件ID', key: 'conflict_id', width: 80 },
  { 
    title: '冲突类型', 
    key: 'conflict_type',
    render(row) {
      return h(NTag, { type: getConflictTypeName(row.conflict_type).type, size: 'small' }, { default: () => getConflictTypeName(row.conflict_type).text });
    }
  },
  { title: '原告', key: 'new_actor_name' },
  { title: '被告', key: 'existing_actor_name' },
  { title: '争议值', key: 'conflicting_value' },
  { title: '检测时间', key: 'detected_at', render: (row) => new Date(row.detected_at).toLocaleString() },
  {
    title: '操作',
    key: 'actions',
    render(row) {
      return h(NButton, { size: 'small', type: 'primary', onClick: () => openResolveModal(row) }, { default: () => '进行裁决' });
    }
  }
]);

// --- Watchers ---
watch(currentConflict, (newVal) => {
  if (newVal) {
    editableConflict.value = JSON.parse(JSON.stringify(newVal));
  }
});

// --- Methods ---
const fetchConflicts = async () => {
  isLoading.value = true;
  error.value = null;
  try {
    const response = await axios.get('/api/actors/conflicts', {
      params: {
        page: pagination.value.page,
        page_size: pagination.value.pageSize,
        query: searchQuery.value,
      }
    });
    conflicts.value = response.data.items;
    pagination.value.itemCount = response.data.total_items;
  } catch (err) {
    error.value = err.response?.data?.error || '获取冲突列表失败。';
    message.error(error.value);
  } finally {
    isLoading.value = false;
  }
};

const handleSearch = () => {
  pagination.value.page = 1;
  fetchConflicts();
};

const startDuplicateScan = async () => {
  isScanning.value = true;
  message.loading("已提交后台任务，开始扫描重复演员...", { duration: 0, key: 'scan-task' });
  try {
    const response = await axios.post('/api/actors/find_duplicates');
    message.success(response.data.message || "扫描任务已成功启动，请稍后刷新列表。");
  } catch (error) {
    message.error(error.response?.data?.error || "启动扫描任务失败！");
  } finally {
    message.destroy('scan-task');
    isScanning.value = false;
  }
};

const openResolveModal = (conflict) => {
  currentConflict.value = conflict;
  showModal.value = true;
};

const resolve = async (action) => {
  if (!currentConflict.value || !editableConflict.value) return;
  const conflictId = currentConflict.value.conflict_id;
  
  const payload = {
    action: action,
    updated_names: {
      new_actor_name: editableConflict.value.new_actor_name,
      existing_actor_name: editableConflict.value.existing_actor_name,
    }
  };
  
  try {
    const response = await axios.post(`/api/actors/resolve_conflict/${conflictId}`, payload);
    message.success(response.data.message || '操作成功！');
    showModal.value = false;
    // 刷新当前页的数据
    fetchConflicts();
  } catch (err) {
    message.error(err.response?.data?.error || '解决冲突时发生错误。');
  }
};

const getConflictTypeName = (type) => {
  if (type === 'POTENTIAL_DUPLICATE') {
    return { text: '潜在重复', type: 'warning' };
  }
  if (type.includes('DOUBAN')) {
    return { text: '豆瓣ID冲突', type: 'info' };
  }
  if (type.includes('IMDB')) {
    return { text: 'IMDb ID冲突', type: 'error' };
  }
  return { text: '未知冲突', type: 'default' };
};

onMounted(() => {
  fetchConflicts();
});
</script>

<style scoped>
.actor-conflicts-page {
  padding: 0 24px 24px 24px;
}
.center-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 300px;
}
.conflict-avatar {
  width: 150px;
  height: 225px;
  object-fit: cover;
  border-radius: 8px;
  background-color: var(--n-action-color);
  cursor: pointer;
  transition: transform 0.2s;
}
.conflict-avatar:hover {
  transform: scale(1.05);
}
</style>
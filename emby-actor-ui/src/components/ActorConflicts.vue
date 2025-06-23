<template>
  <div class="actor-conflicts-page">
    <n-page-header>
      <template #title>
        演员冲突裁决中心
      </template>
      <template #extra>
        <n-button @click="fetchConflicts" :loading="isLoading" circle>
          <template #icon><n-icon :component="RefreshIcon" /></template>
        </n-button>
      </template>
      <n-button @click="startDuplicateScan">扫描重复演员</n-button>
    </n-page-header>
    <n-divider />

    <div v-if="isLoading" class="center-container">
      <n-spin size="large" />
    </div>
    <div v-else-if="error" class="center-container">
      <n-alert title="加载错误" type="error">{{ error }}</n-alert>
    </div>
    <div v-else-if="conflicts.length === 0" class="center-container">
      <n-empty description="天下太平！没有需要您裁决的演员冲突案件。" size="huge" />
    </div>
    <div v-else>
      <n-list bordered hoverable clickable>
        <n-list-item v-for="conflict in conflicts" :key="conflict.conflict_id">
          <template #prefix>
            <n-tag :type="getConflictTypeTag(conflict.conflict_type)" size="small">
              {{ conflict.conflict_type }}
            </n-tag>
          </template>
          
          <n-thing>
            <template #header>
              <n-text strong>
                {{ conflict.new_actor_name }} <small>(TMDb: {{ conflict.new_tmdb_id }})</small>
              </n-text>
            </template>
            <template #header-extra>
              <n-button size="small" type="primary" @click="openResolveModal(conflict)">
                进行裁决
              </n-button>
            </template>
            <template #description>
              尝试关联的 {{ getConflictField(conflict.conflict_type) }}
              <n-tag type="error" size="small" round>{{ conflict.conflicting_value }}</n-tag>
              已被
              <n-text strong>
                {{ conflict.existing_actor_name }} <small>(TMDb: {{ conflict.existing_tmdb_id }})</small>
              </n-text>
              占用。
            </template>
          </n-thing>
        </n-list-item>
      </n-list>
    </div>

    <!-- 裁决模态框 -->
    <n-modal v-model:show="showModal" preset="card" title="冲突裁决" style="width: 80%; max-width: 900px;">
      <div v-if="currentConflict">
        <n-alert :title="`案件 #${currentConflict.conflict_id}`" type="info" style="margin-bottom: 20px;">
          请仔细比对下方两位演员的信息，然后选择一个最合适的操作。
        </n-alert>
        <n-grid cols="1 m:2" :x-gap="20" responsive="screen">
          <!-- “原告”信息 -->
          <n-gi>
            <n-card title="新记录 (原告)">
              <template #header-extra><n-tag type="warning">尝试关联者</n-tag></template>
              <n-space vertical align="center">
                <n-image :src="currentConflict.new_actor_image_url" class="conflict-avatar" />
                <n-h3 style="margin: 0;">{{ currentConflict.new_actor_name }}</n-h3>
                <n-text depth="3">TMDb ID: {{ currentConflict.new_tmdb_id }}</n-text>
              </n-space>
            </n-card>
          </n-gi>
          <!-- “被告”信息 -->
          <n-gi>
            <n-card title="已有记录 (被告)">
              <template #header-extra><n-tag type="success">当前占用者</n-tag></template>
              <n-space vertical align="center">
                <n-image :src="currentConflict.existing_actor_image_url" class="conflict-avatar" />
                <n-h3 style="margin: 0;">{{ currentConflict.existing_actor_name }}</n-h3>
                <n-text depth="3">TMDb ID: {{ currentConflict.existing_tmdb_id }}</n-text>
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

          <n-popconfirm @positive-click="() => resolve('unbind_existing')">
            <template #trigger>
              <n-button type="error">不是同一个人，解绑占用</n-button>
            </template>
            确定要将“被告”的关联解除，让“原告”可以关联吗？
          </n-popconfirm>

          <n-popconfirm @positive-click="() => resolve('ignore')">
            <template #trigger>
              <n-button type="tertiary">忽略此冲突</n-button>
            </template>
            确定要忽略这个冲突吗？它将不再显示。
          </n-popconfirm>
        </n-space>
      </div>
    </n-modal>

  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import axios from 'axios';
import { NPageHeader, NDivider, NList, NListItem, NThing, NTag, NButton, NIcon, NSpin, NAlert, NEmpty, NModal, NCard, NGrid, NGi, NSpace, NImage, NH3, NText, NPopconfirm, useMessage } from 'naive-ui';
import { RefreshOutline as RefreshIcon } from '@vicons/ionicons5';

const isLoading = ref(true);
const error = ref(null);
const conflicts = ref([]);
const message = useMessage();

const showModal = ref(false);
const currentConflict = ref(null);

const fetchConflicts = async () => {
  isLoading.value = true;
  error.value = null;
  try {
    const response = await axios.get('/api/actors/conflicts');
    conflicts.value = response.data;
  } catch (err) {
    error.value = err.response?.data?.error || '获取冲突列表失败。';
    message.error(error.value);
  } finally {
    isLoading.value = false;
  }
};
const startDuplicateScan = async () => {
  message.info("已提交后台任务，开始扫描重复演员...");
  try {
    await axios.post('/api/actors/find_duplicates');
    message.success("扫描任务已成功启动，请稍后刷新冲突列表查看结果。");
  } catch (error) {
    message.error("启动扫描任务失败！");
  }
};
const openResolveModal = (conflict) => {
  currentConflict.value = conflict;
  showModal.value = true;
};

const resolve = async (action) => {
  if (!currentConflict.value) return;
  const conflictId = currentConflict.value.conflict_id;
  
  try {
    const response = await axios.post(`/api/actors/resolve_conflict/${conflictId}`, { action });
    message.success(response.data.message || '操作成功！');
    showModal.value = false;
    // 从列表中移除已解决的冲突
    conflicts.value = conflicts.value.filter(c => c.conflict_id !== conflictId);
  } catch (err) {
    message.error(err.response?.data?.error || '解决冲突时发生错误。');
  }
};

const getConflictTypeTag = (type) => {
  if (type.includes('DOUBAN')) return 'info';
  if (type.includes('IMDB')) return 'warning';
  return 'default';
};

const getConflictField = (type) => {
  if (type.includes('DOUBAN')) return '豆瓣ID';
  if (type.includes('IMDB')) return 'IMDb ID';
  return '值';
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
}
</style>
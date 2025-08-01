<!-- src/components/CustomCollectionsManager.vue (最终独立版) -->
<template>
  <n-layout content-style="padding: 24px;">
    <div class="custom-collections-manager">
      <!-- 1. 页面头部 -->
      <n-page-header>
        <template #title>
          自建合集
        </template>
        <template #extra>
          <n-space>
            <n-tooltip>
              <template #trigger>
                <n-button @click="triggerMetadataSync" :loading="isSyncingMetadata" circle>
                  <template #icon><n-icon :component="SyncIcon" /></template>
                </n-button>
              </template>
              快速同步媒体元数据
            </n-tooltip>
            <n-button type="primary" ghost @click="handleSyncAll" :loading="isSyncingAll">
              <template #icon><n-icon :component="GenerateIcon" /></template>
              一键生成所有
            </n-button>
            <n-button type="primary" @click="handleCreateClick">
              <template #icon><n-icon :component="AddIcon" /></template>
              创建新合集
            </n-button>
          </n-space>
        </template>
        <template #footer>
          <n-alert title="操作提示" type="info" :bordered="false">
            <ul style="margin: 0; padding-left: 20px;">
              <li>在这里创建和管理通过RSS榜单或自定义规则生成的“自建合集”。</li>
              <li>在创建或生成“筛选规则”合集前，请点击 <n-icon :component="SyncIcon" /> 按钮快速同步一次最新的媒体库元数据。</li>
            </ul>
          </n-alert>
        </template>
      </n-page-header>

      <!-- 2. 数据表格 -->
      <n-data-table
        :columns="columns"
        :data="collections"
        :loading="isLoading"
        :bordered="false"
        :single-line="false"
        style="margin-top: 24px;"
      />
    </div>

    <!-- 3. 创建/编辑模态框 -->
    <n-modal
      v-model:show="showModal"
      preset="card"
      style="width: 90%; max-width: 700px;"
      :title="isEditing ? '编辑合集' : '创建新合集'"
      :bordered="false"
      size="huge"
    >
      <n-form
        ref="formRef"
        :model="currentCollection"
        :rules="formRules"
        label-placement="left"
        label-width="auto"
      >
        <n-form-item label="合集名称" path="name">
          <n-input v-model:value="currentCollection.name" placeholder="例如：周星驰系列" />
        </n-form-item>
        
        <n-form-item label="合集类型" path="type">
          <n-select
            v-model:value="currentCollection.type"
            :options="typeOptions"
            :disabled="isEditing"
            placeholder="请选择合集类型"
          />
        </n-form-item>

        <n-form-item v-if="currentCollection.type" label="合集内容" path="definition.item_type">
        <n-radio-group v-model:value="currentCollection.definition.item_type">
            <n-space>
            <n-radio value="Movie">电影</n-radio>
            <n-radio value="Series">电视剧</n-radio>
            </n-space>
        </n-radio-group>
        </n-form-item>

        <!-- 榜单导入 (List) 类型的表单 -->
        <div v-if="currentCollection.type === 'list'">
          <n-form-item label="榜单URL" path="definition.url">
              <n-input v-model:value="currentCollection.definition.url" placeholder="请输入RSS源的URL" />
              <template #feedback>
              请输入一个有效的RSS订阅源地址。
              </template>
          </n-form-item>
        </div>

        <!-- 筛选规则 (Filter) 类型的表单 -->
        <div v-if="currentCollection.type === 'filter'">
          <n-form-item label="匹配逻辑">
            <n-radio-group v-model:value="currentCollection.definition.logic">
              <n-space>
                <n-radio value="AND">满足所有条件 (AND)</n-radio>
                <n-radio value="OR">满足任一条件 (OR)</n-radio>
              </n-space>
            </n-radio-group>
          </n-form-item>

          <n-form-item label="筛选规则" path="definition.rules">
            <div style="width: 100%;">
              <n-space v-for="(rule, index) in currentCollection.definition.rules" :key="index" style="margin-bottom: 12px;" align="center">
                <n-select v-model:value="rule.field" :options="fieldOptions" placeholder="字段" style="width: 150px;" clearable />
                <n-select v-model:value="rule.operator" :options="getOperatorOptionsForRow(rule)" placeholder="操作" style="width: 120px;" :disabled="!rule.field" clearable />
                <n-select
                    v-if="rule.field === 'countries'"
                    v-model:value="rule.value"
                    :options="countryOptions"
                    placeholder="选择地区"
                    :disabled="!rule.operator"
                    filterable
                />
                <n-select
                    v-else-if="rule.field === 'genres'"
                    v-model:value="rule.value"
                    :options="genreOptions"
                    placeholder="选择类型"
                    :disabled="!rule.operator"
                    filterable
                />
                <n-select
                    v-else-if="rule.field === 'studios'"
                    v-model:value="rule.value"
                    :options="studioOptions"
                    placeholder="输入以搜索工作室"
                    :disabled="!rule.operator"
                    filterable
                    remote
                    :loading="isSearchingStudios"
                    @search="handleStudioSearch"
                />
                <n-input v-else v-model:value="rule.value" placeholder="值" :disabled="!rule.operator" />
                <n-button text type="error" @click="removeRule(index)">
                  <template #icon><n-icon :component="DeleteIcon" /></template>
                </n-button>
              </n-space>
              <n-button @click="addRule" dashed block>
                <template #icon><n-icon :component="AddIcon" /></template>
                添加条件
              </n-button>
            </div>
          </n-form-item>
        </div>
        
        <n-form-item label="状态" path="status" v-if="isEditing">
            <n-radio-group v-model:value="currentCollection.status">
                <n-space>
                    <n-radio value="active">启用</n-radio>
                    <n-radio value="paused">暂停</n-radio>
                </n-space>
            </n-radio-group>
        </n-form-item>

      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="showModal = false">取消</n-button>
          <n-button type="primary" @click="handleSave" :loading="isSaving">保存</n-button>
        </n-space>
      </template>
    </n-modal>
    
    <!-- ★★★ 缺失详情查看模态框 (已完全适配新API) ★★★ -->
    <n-modal v-model:show="showDetailsModal" preset="card" style="width: 90%; max-width: 1200px;" :title="detailsModalTitle" :bordered="false" size="huge">
      <div v-if="isLoadingDetails" class="center-container"><n-spin size="large" /></div>
      <div v-else-if="selectedCollectionDetails">
        <n-tabs type="line" animated>
          <n-tab-pane name="missing" :tab="`缺失${mediaTypeName} (${missingMediaInModal.length})`">
            <n-empty v-if="missingMediaInModal.length === 0" :description="`太棒了！没有已上映的缺失${mediaTypeName}。`" style="margin-top: 40px;"></n-empty>
            <n-grid v-else cols="2 s:3 m:4 l:5 xl:6" :x-gap="16" :y-gap="16" responsive="screen">
              <n-gi v-for="media in missingMediaInModal" :key="media.tmdb_id">
                <n-card class="movie-card" content-style="padding: 0;">
                  <template #cover><img :src="getTmdbImageUrl(media.poster_path)" class="movie-poster" /></template>
                  <div class="movie-info"><div class="movie-title">{{ media.title }}<br />({{ extractYear(media.release_date) || '未知年份' }})</div></div>
                  <template #action>
                    <n-button @click="subscribeMedia(media)" type="primary" size="small" block :loading="subscribing[media.tmdb_id]">
                      <template #icon><n-icon :component="CloudDownloadIcon" /></template>
                      订阅
                    </n-button>
                  </template>
                </n-card>
              </n-gi>
            </n-grid>
          </n-tab-pane>
          
          <n-tab-pane name="in_library" :tab="`已入库 (${inLibraryMediaInModal.length})`">
             <n-empty v-if="inLibraryMediaInModal.length === 0" :description="`该合集在媒体库中没有任何${mediaTypeName}。`" style="margin-top: 40px;"></n-empty>
            <n-grid v-else cols="2 s:3 m:4 l:5 xl:6" :x-gap="16" :y-gap="16" responsive="screen">
              <n-gi v-for="media in inLibraryMediaInModal" :key="media.tmdb_id">
                <n-card class="movie-card" content-style="padding: 0;">
                  <template #cover><img :src="getTmdbImageUrl(media.poster_path)" class="movie-poster" /></template>
                  <div class="movie-info"><div class="movie-title">{{ media.title }}<br />({{ extractYear(media.release_date) || '未知年份' }})</div></div>
                   <template #action>
                    <n-tag type="success" size="small" style="width: 100%; justify-content: center;">
                      <template #icon><n-icon :component="CheckmarkCircle" /></template>
                      已在库
                    </n-tag>
                  </template>
                </n-card>
              </n-gi>
            </n-grid>
          </n-tab-pane>

          <n-tab-pane name="unreleased" :tab="`未上映 (${unreleasedMediaInModal.length})`">
            <n-empty v-if="unreleasedMediaInModal.length === 0" :description="`该合集没有已知的未上映${mediaTypeName}。`" style="margin-top: 40px;"></n-empty>
            <n-grid v-else cols="2 s:3 m:4 l:5 xl:6" :x-gap="16" :y-gap="16" responsive="screen">
              <n-gi v-for="media in unreleasedMediaInModal" :key="media.tmdb_id">
                <n-card class="movie-card" content-style="padding: 0;">
                  <template #cover><img :src="getTmdbImageUrl(media.poster_path)" class="movie-poster"></template>
                  <div class="movie-info"><div class="movie-title">{{ media.title }}<br />({{ extractYear(media.release_date) || '未知年份' }})</div></div>
                </n-card>
              </n-gi>
            </n-grid>
          </n-tab-pane>

          <n-tab-pane name="subscribed" :tab="`已订阅 (${subscribedMediaInModal.length})`">
            <n-empty v-if="subscribedMediaInModal.length === 0" :description="`你没有订阅此合集中的任何${mediaTypeName}。`" style="margin-top: 40px;"></n-empty>
            <n-grid v-else cols="2 s:3 m:4 l:5 xl:6" :x-gap="16" :y-gap="16" responsive="screen">
              <n-gi v-for="media in subscribedMediaInModal" :key="media.tmdb_id">
                <n-card class="movie-card" content-style="padding: 0;">
                  <template #cover><img :src="getTmdbImageUrl(media.poster_path)" class="movie-poster" /></template>
                  <div class="movie-info"><div class="movie-title">{{ media.title }}<br />({{ extractYear(media.release_date) || '未知年份' }})</div></div>
                  <template #action>
                    <n-button @click="updateMediaStatus(media, 'missing')" type="warning" size="small" block ghost>
                      <template #icon><n-icon :component="CloseCircleIcon" /></template>
                      取消订阅
                    </n-button>
                  </template>
                </n-card>
              </n-gi>
            </n-grid>
          </n-tab-pane>
        </n-tabs>
      </div>
    </n-modal>
  </n-layout>
</template>

<script setup>
import { ref, onMounted, h, computed, watch } from 'vue';
import axios from 'axios';
import { 
  NLayout, NPageHeader, NButton, NIcon, NText, NDataTable, NTag, NSpace,
  useMessage, NPopconfirm, NModal, NForm, NFormItem, NInput, NSelect,
  NAlert, NRadioGroup, NRadio, NTooltip, NSpin, NGrid, NGi, NCard, NEmpty, NTabs, NTabPane
} from 'naive-ui';
import { 
  AddOutline as AddIcon, 
  CreateOutline as EditIcon, 
  TrashOutline as DeleteIcon,
  SyncOutline as SyncIcon,
  EyeOutline as EyeIcon,
  PlayOutline as GenerateIcon,
  CloudDownloadOutline as CloudDownloadIcon,
  CheckmarkCircleOutline as CheckmarkCircle,
  CloseCircleOutline as CloseCircleIcon
} from '@vicons/ionicons5';
import { format } from 'date-fns';

const message = useMessage();
const collections = ref([]);
const isLoading = ref(true);
const showModal = ref(false);
const isEditing = ref(false);
const isSaving = ref(false);
const formRef = ref(null);
const syncLoading = ref({});
const isSyncingMetadata = ref(false);
const countryOptions = ref([]);
const isSyncingAll = ref(false);
const genreOptions = ref([]);
const studioOptions = ref([]);
const isSearchingStudios = ref(false);
const showDetailsModal = ref(false);
const isLoadingDetails = ref(false);
const selectedCollectionDetails = ref(null);
const subscribing = ref({});

const getInitialFormModel = () => ({
  id: null,
  name: '',
  type: 'list',
  status: 'active',
  definition: {
    item_type: 'Movie',
    url: '' 
  }
});
const currentCollection = ref(getInitialFormModel());

watch(() => currentCollection.value.type, (newType) => {
  if (isEditing.value) { return; }
  if (newType === 'filter') {
    currentCollection.value.definition = {
      item_type: 'Movie', 
      logic: 'AND',
      rules: [{ field: null, operator: null, value: '' }]
    };
  } else if (newType === 'list') {
    currentCollection.value.definition = { 
      item_type: 'Movie',
      url: '' 
    };
  }
});

const fetchCountryOptions = async () => {
  try {
    const response = await axios.get('/api/config/countries');
    const countryMap = response.data;
    countryOptions.value = Object.values(countryMap).map(name => ({
      label: name,
      value: name
    })).sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'));
  } catch (error) {
    message.error('获取国家/地区列表失败。');
  }
};

const fetchGenreOptions = async () => {
  try {
    const response = await axios.get('/api/config/genres');
    const genreList = response.data; 
    genreOptions.value = genreList.map(name => ({
      label: name,
      value: name
    }));
  } catch (error) {
    message.error('获取电影类型列表失败。');
  }
};

const handleStudioSearch = async (query) => {
  if (!query) { 
    studioOptions.value = [];
    return;
  }
  isSearchingStudios.value = true;
  try {
    const response = await axios.get(`/api/search_studios?q=${query}`);
    studioOptions.value = response.data.map(name => ({ label: name, value: name }));
  } catch (error) {
    console.error('搜索工作室失败:', error);
  } finally {
    isSearchingStudios.value = false;
  }
};

watch(() => currentCollection.value.definition.rules, (newRules) => {
  if (Array.isArray(newRules)) {
    newRules.forEach(rule => {
      const validOperators = getOperatorOptionsForRow(rule).map(opt => opt.value);
      if (rule.operator && !validOperators.includes(rule.operator)) {
        rule.operator = null;
        rule.value = '';
      }
      if (rule.field && !rule.operator) {
        const options = getOperatorOptionsForRow(rule);
        if (options && options.length === 1) {
          rule.operator = options[0].value;
        }
      }
    });
  }
}, { deep: true });

const ruleConfig = {
  actors: { label: '演员', type: 'text', operators: ['contains'] },
  directors: { label: '导演', type: 'text', operators: ['contains'] },
  release_year: { label: '年份', type: 'number', operators: ['gte', 'lte', 'eq'] },
  genres: { label: '类型', type: 'select', operators: ['contains'] },
  countries: { label: '国家/地区', type: 'select', operators: ['contains'] },
  studios: { label: '工作室', type: 'select', operators: ['contains'] },
};

const operatorLabels = {
  contains: '包含', gte: '大于等于', lte: '小于等于', eq: '等于',
};

const fieldOptions = computed(() => 
  Object.keys(ruleConfig).map(key => ({ label: ruleConfig[key].label, value: key }))
);

const getOperatorOptionsForRow = (rule) => {
  if (!rule.field) return [];
  return (ruleConfig[rule.field]?.operators || []).map(op => ({ label: operatorLabels[op] || op, value: op }));
};

const addRule = () => {
  currentCollection.value.definition.rules?.push({ field: null, operator: null, value: '' });
};

const removeRule = (index) => {
  currentCollection.value.definition.rules?.splice(index, 1);
};

const typeOptions = [
  { label: '通过榜单导入 (RSS)', value: 'list' },
  { label: '通过筛选规则生成', value: 'filter' }
];

const formRules = computed(() => {
  const baseRules = {
    name: { required: true, message: '请输入合集名称', trigger: 'blur' },
    type: { required: true, message: '请选择合集类型' },
    'definition.item_type': { required: true, message: '请选择合集内容类型' }
  };
  if (currentCollection.value.type === 'list') {
    baseRules['definition.url'] = { required: true, message: '请输入榜单的URL', trigger: 'blur' };
  } else if (currentCollection.value.type === 'filter') {
    baseRules['definition.rules'] = {
      type: 'array', required: true,
      validator: (rule, value) => {
        if (!value || value.length === 0) return new Error('请至少添加一条筛选规则');
        if (value.some(r => !r.field || !r.operator || !r.value)) return new Error('请将所有规则填写完整');
        return true;
      },
      trigger: 'change'
    };
  }
  return baseRules;
});

const detailsModalTitle = computed(() => {
  if (!selectedCollectionDetails.value) return '';
  const typeLabel = selectedCollectionDetails.value.item_type === 'Series' ? '电视剧合集' : '电影合集';
  return `${typeLabel}详情 - ${selectedCollectionDetails.value.name}`;
});

const mediaTypeName = computed(() => {
  if (!selectedCollectionDetails.value) return '媒体';
  return selectedCollectionDetails.value.item_type === 'Series' ? '剧集' : '影片';
});

const filterMediaByStatus = (status) => {
  if (!selectedCollectionDetails.value || !Array.isArray(selectedCollectionDetails.value.media_items)) return [];
  return selectedCollectionDetails.value.media_items.filter(media => media.status === status);
};

const missingMediaInModal = computed(() => filterMediaByStatus('missing'));
const inLibraryMediaInModal = computed(() => filterMediaByStatus('in_library'));
const unreleasedMediaInModal = computed(() => filterMediaByStatus('unreleased'));
const subscribedMediaInModal = computed(() => filterMediaByStatus('subscribed'));

const fetchCollections = async () => {
  isLoading.value = true;
  try {
    const response = await axios.get('/api/custom_collections');
    collections.value = response.data;
  } catch (error) {
    message.error('加载自定义合集列表失败。');
  } finally {
    isLoading.value = false;
  }
};

const openDetailsModal = async (collection) => {
  showDetailsModal.value = true;
  isLoadingDetails.value = true;
  selectedCollectionDetails.value = null;
  try {
    const response = await axios.get(`/api/custom_collections/${collection.id}/status`);
    selectedCollectionDetails.value = response.data;
  } catch (error) {
    message.error('获取合集详情失败。');
    showDetailsModal.value = false;
  } finally {
    isLoadingDetails.value = false;
  }
};

const subscribeMedia = async (media) => {
  subscribing.value[media.tmdb_id] = true;
  try {
    await axios.post('/api/collections/subscribe', { tmdb_id: media.tmdb_id, title: media.title, item_type: selectedCollectionDetails.value.item_type });
    message.success(`《${media.title}》已提交订阅`);
    media.status = 'subscribed';
  } catch (err) {
    message.error(err.response?.data?.error || '订阅失败');
  } finally {
    subscribing.value[media.tmdb_id] = false;
  }
};

const handleSyncAll = async () => {
  isSyncingAll.value = true;
  try {
    const response = await axios.post('/api/custom_collections/sync_all');
    message.success(response.data.message || '已提交一键生成任务！');
  } catch (error) {
    message.error(error.response?.data?.error || '提交任务失败。');
  } finally {
    isSyncingAll.value = false;
  }
};

const updateMediaStatus = async (media, newStatus) => {
  try {
    await axios.post(`/api/custom_collections/${selectedCollectionDetails.value.id}/media_status`, {
      tmdb_id: media.tmdb_id,
      new_status: newStatus
    });
    media.status = newStatus;
    message.success(`状态已更新为: ${newStatus}`);
  } catch (err) {
    message.error(err.response?.data?.error || '更新状态失败');
  }
};

const triggerMetadataSync = async () => {
  isSyncingMetadata.value = true;
  try {
    const response = await axios.post('/api/tasks/trigger/populate-metadata');
    message.success(response.data.message || '快速同步元数据任务已在后台启动！');
  } catch (error) {
    message.error(error.response?.data?.error || '启动任务失败。');
  } finally {
    isSyncingMetadata.value = false;
  }
};

const handleCreateClick = () => {
  isEditing.value = false;
  currentCollection.value = getInitialFormModel();
  showModal.value = true;
};

const handleEditClick = (row) => {
  isEditing.value = true;
  const rowCopy = JSON.parse(JSON.stringify(row));
  try {
    rowCopy.definition = JSON.parse(rowCopy.definition_json);
  } catch {
    rowCopy.definition = rowCopy.type === 'filter' 
      ? { item_type: 'Movie', logic: 'AND', rules: [] } 
      : { item_type: 'Movie', url: '' };
  }
  delete rowCopy.definition_json;
  currentCollection.value = rowCopy;
  showModal.value = true;
};

const handleDelete = async (row) => {
  try {
    await axios.delete(`/api/custom_collections/${row.id}`);
    message.success(`合集 "${row.name}" 已删除。`);
    fetchCollections();
  } catch (error) {
    message.error('删除失败。');
  }
};

const handleSync = async (row) => {
  syncLoading.value[row.id] = true;
  try {
    const response = await axios.post(`/api/custom_collections/${row.id}/sync`);
    message.success(response.data.message || `已提交同步任务: ${row.name}`);
  } catch (error) {
    message.error(error.response?.data?.error || '提交同步任务失败。');
  } finally {
    syncLoading.value[row.id] = false;
  }
};

const handleSave = () => {
  formRef.value?.validate(async (errors) => {
    if (errors) return;
    isSaving.value = true;
    const dataToSend = JSON.parse(JSON.stringify(currentCollection.value));
    try {
      if (isEditing.value) {
        await axios.put(`/api/custom_collections/${dataToSend.id}`, dataToSend);
        message.success('合集更新成功！');
      } else {
        await axios.post('/api/custom_collections', dataToSend);
        message.success('合集创建成功！');
      }
      showModal.value = false;
      fetchCollections();
    } catch (error) {
      message.error(error.response?.data?.error || '保存失败。');
    } finally {
      isSaving.value = false;
    }
  });
};

const columns = [
  { title: '名称', key: 'name', width: 250, ellipsis: { tooltip: true } },
  { 
    title: '类型', key: 'type', width: 120,
    render: (row) => h(NTag, { type: row.type === 'list' ? 'info' : 'default', bordered: false }, { default: () => row.type === 'list' ? '榜单导入' : '筛选生成' })
  },
  {
    title: '内容', key: 'item_type', width: 100,
    render: (row) => {
        const itemType = row.item_type || JSON.parse(row.definition_json)?.item_type || 'Movie';
        return h(NTag, { bordered: false }, { default: () => itemType === 'Series' ? '电视剧' : '电影' });
    }
  },
  {
    title: '健康检查', key: 'health_check', width: 150,
    render(row) {
      if (row.type !== 'list' || !row.emby_collection_id) {
        return h(NText, { depth: 3 }, { default: () => 'N/A' });
      }
      const missingText = row.missing_count > 0 ? ` (${row.missing_count}缺失)` : '';
      const buttonType = row.missing_count > 0 ? 'warning' : 'default';
      return h(NButton, {
        size: 'small', type: buttonType, ghost: true,
        onClick: () => openDetailsModal(row)
      }, { default: () => `查看详情${missingText}`, icon: () => h(NIcon, { component: EyeIcon }) });
    }
  },
  { 
    title: '状态', key: 'status', width: 90,
    render: (row) => h(NTag, { type: row.status === 'active' ? 'success' : 'warning', bordered: false }, { default: () => row.status === 'active' ? '启用' : '暂停' })
  },
  { 
    title: '上次同步', key: 'last_synced_at', width: 180,
    render: (row) => row.last_synced_at ? format(new Date(row.last_synced_at), 'yyyy-MM-dd HH:mm') : '从未'
  },
  {
    title: '操作', key: 'actions', fixed: 'right', width: 220,
    render: (row) => h(NSpace, null, {
      default: () => [
        h(NButton, { size: 'small', type: 'primary', ghost: true, loading: syncLoading.value[row.id], onClick: () => handleSync(row) }, { icon: () => h(NIcon, { component: GenerateIcon }), default: () => '生成' }),
        h(NButton, { size: 'small', onClick: () => handleEditClick(row) }, { icon: () => h(NIcon, { component: EditIcon }), default: () => '编辑' }),
        h(NPopconfirm, { onPositiveClick: () => handleDelete(row) }, {
          trigger: () => h(NButton, { size: 'small', type: 'error', ghost: true }, { icon: () => h(NIcon, { component: DeleteIcon }), default: () => '删除' }),
          default: () => `确定删除合集 "${row.name}" 吗？`
        })
      ]
    })
  }
];

const getTmdbImageUrl = (posterPath) => posterPath ? `https://image.tmdb.org/t/p/w300${posterPath}` : '/img/poster-placeholder.png';
const extractYear = (dateStr) => dateStr ? dateStr.substring(0, 4) : null;

onMounted(() => {
  fetchCollections();
  fetchCountryOptions();
  fetchGenreOptions();
});
</script>

<style scoped>
.custom-collections-manager {
  padding: 0 10px;
}
.movie-card {
  overflow: hidden;
  border-radius: 8px;
}
.movie-poster {
  width: 100%;
  height: auto;
  aspect-ratio: 2 / 3;
  object-fit: cover;
}
.movie-info {
  padding: 8px;
  text-align: center;
}
.movie-title {
  font-size: 13px;
  line-height: 1.3;
  height: 3.9em; /* 3 lines */
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  line-clamp: 3;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}
.center-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 200px;
}
</style>
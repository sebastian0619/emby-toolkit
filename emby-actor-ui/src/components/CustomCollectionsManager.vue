<!-- src/components/CustomCollectionsManager.vue (最终版) -->
<template>
  <n-layout content-style="padding: 24px;">
    <div class="custom-collections-manager">
      <!-- 1. 页面头部 (保持不变) -->
      <n-page-header>
        <template #title>
          自建合集
        </template>
        <template #extra>
          <!-- ★★★ 核心修改：将两个操作按钮放在一起 ★★★ -->
          <n-space>
            <n-tooltip>
              <template #trigger>
                <n-button @click="triggerMetadataSync" :loading="isSyncingMetadata" circle>
                  <template #icon><n-icon :component="SyncIcon" /></template>
                </n-button>
              </template>
              快速同步媒体元数据
            </n-tooltip>

            <n-button type="primary" @click="handleCreateClick">
              <template #icon><n-icon :component="AddIcon" /></template>
              创建新合集
            </n-button>
          </n-space>
        </template>
        <template #footer>
          <!-- ★★★ 核心修改：更新说明文字 ★★★ -->
          <n-alert title="操作提示" type="info" :bordered="false">
            <ul style="margin: 0; padding-left: 20px;">
              <li>在这里创建和管理通过RSS榜单或自定义规则生成的“自建合集”。</li>
              <li>在创建或生成“筛选规则”合集前，请点击 <n-icon :component="SyncIcon" /> 按钮快速同步一次最新的媒体库元数据。</li>
            </ul>
          </n-alert>
        </template>
      </n-page-header>

      <!-- 2. 数据表格 (保持不变) -->
      <n-data-table
        :columns="columns"
        :data="collections"
        :loading="isLoading"
        :bordered="false"
        :single-line="false"
        style="margin-top: 24px;"
      />
    </div>

    <!-- 3. 创建/编辑模态框 (核心升级区域) -->
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

        <n-form-item v-if="currentCollection.type === 'filter'" label="合集内容" path="definition.item_type">
          <n-radio-group v-model:value="currentCollection.definition.item_type">
            <n-space>
             <n-radio value="Movie">电影</n-radio>
             <n-radio value="Series">电视剧</n-radio>
            </n-space>
          </n-radio-group>
        </n-form-item>

        <!-- ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★ -->
        <!-- ★★★ 核心升级: 动态表单区域 ★★★ -->
        <!-- ★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★★ -->

        <!-- 榜单导入 (List) 类型的表单 -->
        <div v-if="currentCollection.type === 'list'">
          <n-form-item label="榜单URL" path="definition.url">
            <n-input v-model:value="currentCollection.definition.url" placeholder="请输入RSS源的URL" />
            <template #feedback>
              目前仅支持标准的RSS格式，例如 IMDb Top 250 的RSS源。
            </template>
          </n-form-item>
        </div>

        <!-- 筛选规则 (Filter) 类型的表单 -->
        <div v-if="currentCollection.type === 'filter'">
          <!-- 逻辑关系 -->
          <n-form-item label="匹配逻辑">
            <n-radio-group v-model:value="currentCollection.definition.logic">
              <n-space>
                <n-radio value="AND">满足所有条件 (AND)</n-radio>
                <n-radio value="OR">满足任一条件 (OR)</n-radio>
              </n-space>
            </n-radio-group>
          </n-form-item>

          <!-- 规则列表 -->
          <n-form-item label="筛选规则" path="definition.rules">
            <div style="width: 100%;">
              <n-space v-for="(rule, index) in currentCollection.definition.rules" :key="index" style="margin-bottom: 12px;" align="center">
                <!-- 字段选择 -->
                <n-select v-model:value="rule.field" :options="fieldOptions" placeholder="字段" style="width: 150px;" clearable />
                <!-- 操作符选择 -->
                <n-select v-model:value="rule.operator" :options="getOperatorOptionsForRow(rule)" placeholder="操作" style="width: 120px;" :disabled="!rule.field" clearable />
                <!-- 如果字段是“国家/地区”，则显示下拉选择框 -->
                <n-select
                    v-if="rule.field === 'countries'"
                    v-model:value="rule.value"
                    :options="countryOptions"
                    placeholder="选择地区"
                    :disabled="!rule.operator"
                    filterable
                />
                <!-- ★★★ 新增：为“类型”也添加下拉选择框 ★★★ -->
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
                <!-- 值输入 -->
                <n-input v-model:value="rule.value" placeholder="值" :disabled="!rule.operator" />
                <!-- 删除按钮 -->
                <n-button text type="error" @click="removeRule(index)">
                  <template #icon><n-icon :component="DeleteIcon" /></template>
                </n-button>
              </n-space>
              <!-- 添加规则按钮 -->
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
  </n-layout>
</template>

<script setup>
import { ref, onMounted, h, computed, watch } from 'vue';
import axios from 'axios';
import { 
  NLayout, NPageHeader, NButton, NIcon, NText, NDataTable, NTag, NSpace,
  useMessage, NPopconfirm, NModal, NForm, NFormItem, NInput, NSelect,
  NAlert, NRadioGroup, NRadio, NTooltip
} from 'naive-ui';
import { 
  AddOutline as AddIcon, 
  CreateOutline as EditIcon, 
  TrashOutline as DeleteIcon,
  SyncOutline as SyncIcon,
  PlayOutline as GenerateIcon
} from '@vicons/ionicons5';
import { format } from 'date-fns';

// --- 核心状态定义 (保持不变) ---
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
const genreOptions = ref([]);
const studioOptions = ref([]);
const isSearchingStudios = ref(false);

// --- ★★★ 升级版表单数据模型 ★★★ ---
const getInitialFormModel = () => ({
  id: null,
  name: '',
  type: 'list',
  status: 'active',
  definition: {
    url: '' 
  }
});
const currentCollection = ref(getInitialFormModel());

// ★★★ 监听类型变化，动态切换 definition 结构 ★★★
watch(() => currentCollection.value.type, (newType) => {
  // ★★★ 核心修复：只有在“创建”模式下，才执行重置逻辑 ★★★
  if (isEditing.value) {
    return; // 如果是编辑模式，直接退出，不要做任何事！
  }

  // 下面的逻辑现在只会在 isEditing.value 为 false 时运行
  if (newType === 'filter') {
    currentCollection.value.definition = {
      item_type: 'Movie', 
      logic: 'AND',
      rules: [{ field: null, operator: null, value: '' }]
    };
  } else if (newType === 'list') {
    currentCollection.value.definition = { url: '' };
  }
});

// ★★★ 创建一个供下拉菜单使用的选项列表 ★★★
const fetchCountryOptions = async () => {
  try {
    const response = await axios.get('/api/config/countries');
    const countryMap = response.data;
    // 将后端返回的 { "Eng": "中文" } 格式转换为下拉菜单需要的 { label, value } 格式
    countryOptions.value = Object.values(countryMap).map(name => ({
      label: name,
      value: name
    })).sort((a, b) => a.label.localeCompare(b.label, 'zh-CN')); // 按中文拼音排序
  } catch (error) {
    message.error('获取国家/地区列表失败。');
    countryOptions.value = [];
  }
};

const fetchGenreOptions = async () => {
  try {
    const response = await axios.get('/api/config/genres');
    // ★★★ 核心修复：后端现在直接返回一个字符串数组 ★★★
    const genreList = response.data; 
    genreOptions.value = genreList.map(name => ({
      label: name,
      value: name
    })); // .sort() 也可以去掉，因为后端已经排序好了
  } catch (error) {
    message.error('获取电影类型列表失败。');
    genreOptions.value = [];
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
    const studioList = response.data;
    studioOptions.value = studioList.map(name => ({
      label: name,
      value: name
    }));
  } catch (error) {
    // 不再用 message.error 刷屏，只在控制台记录
    console.error('搜索工作室失败:', error);
    studioOptions.value = [];
  } finally {
    isSearchingStudios.value = false;
  }
};

// ★★★ 核心升级：创建一个 watch 来监听字段的变化 ★★★
watch(() => currentCollection.value.definition.rules, (newRules) => {
  if (Array.isArray(newRules)) {
    newRules.forEach(rule => {
      // 步骤 1: 验证一致性。如果当前的操作符对于当前字段是无效的，就清空它！
      const validOperators = getOperatorOptionsForRow(rule).map(opt => opt.value);
      if (rule.operator && !validOperators.includes(rule.operator)) {
        rule.operator = null;
        rule.value = ''; // 清空操作符时，最好也把值一起清空
      }

      // 步骤 2: 在确保状态一致后，再执行我们的智能自动选择逻辑
      if (rule.field && !rule.operator) {
        const options = getOperatorOptionsForRow(rule);
        if (options && options.length === 1) {
          rule.operator = options[0].value;
        }
      }
    });
  }
}, { deep: true });

// --- ★★★ 规则构建器的配置 ★★★ ---
const ruleConfig = {
  actors: { label: '演员', type: 'text', operators: ['contains'] },
  directors: { label: '导演', type: 'text', operators: ['contains'] },
  release_year: { label: '年份', type: 'number', operators: ['gte', 'lte', 'eq'] },
  genres: { label: '类型', type: 'select', operators: ['contains'] },
  countries: { label: '国家/地区', type: 'select', operators: ['contains'] },
  studios: { label: '工作室', type: 'select', operators: ['contains'] },
};

const operatorLabels = {
  contains: '包含',
  gte: '大于等于',
  lte: '小于等于',
  eq: '等于',
};

const fieldOptions = computed(() => 
  Object.keys(ruleConfig).map(key => ({
    label: ruleConfig[key].label,
    value: key
  }))
);

const getOperatorOptionsForRow = (rule) => {
  if (!rule.field) return [];
  const operators = ruleConfig[rule.field]?.operators || [];
  return operators.map(op => ({
    label: operatorLabels[op] || op,
    value: op
  }));
};

// --- ★★★ 规则操作函数 ★★★ ---
const addRule = () => {
  if (currentCollection.value.definition.rules) {
    currentCollection.value.definition.rules.push({ field: null, operator: null, value: '' });
  }
};

const removeRule = (index) => {
  if (currentCollection.value.definition.rules) {
    currentCollection.value.definition.rules.splice(index, 1);
  }
};

// --- 表单与表格的静态配置 (升级版) ---
const typeOptions = [
  { label: '通过榜单导入 (RSS)', value: 'list' },
  { label: '通过筛选规则生成', value: 'filter' }
];

const formRules = computed(() => {
  const baseRules = {
    name: { required: true, message: '请输入合集名称', trigger: 'blur' },
    type: { required: true, message: '请选择合集类型' },
  };
  if (currentCollection.value.type === 'list') {
    baseRules.definition = { url: { required: true, message: '请输入榜单的URL', trigger: 'blur' } };
  } else if (currentCollection.value.type === 'filter') {
    baseRules.definition = {
      rules: {
        type: 'array',
        required: true,
        validator: (rule, value) => {
          if (!value || value.length === 0) {
            return new Error('请至少添加一条筛选规则');
          }
          for (const r of value) {
            if (!r.field || !r.operator || !r.value) {
              return new Error('请将所有规则填写完整');
            }
          }
          return true;
        },
        trigger: 'change'
      }
    };
  }
  return baseRules;
});

// --- API 调用函数 (保持不变) ---
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

// --- 触发“快速同步元数据”任务的函数 ---
const triggerMetadataSync = async () => {
  isSyncingMetadata.value = true;
  try {
    // 我们直接调用在 tasks.py 中注册的那个任务的 key
    const response = await axios.post('/api/tasks/trigger/populate-metadata');
    message.success(response.data.message || '快速同步元数据任务已在后台启动！');
  } catch (error) {
    message.error(error.response?.data?.error || '启动任务失败。');
  } finally {
    isSyncingMetadata.value = false;
  }
};

// --- 事件处理函数 (handleEditClick 升级) ---
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
    // 如果解析失败，根据类型初始化
    rowCopy.definition = rowCopy.type === 'filter' ? { logic: 'AND', rules: [] } : { url: '' };
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
    if (!errors) {
      isSaving.value = true;
      
      // ★★★ 核心修复：不再使用可能不完整的 payload ★★★
      // 直接使用 currentCollection.value 作为要发送的数据源，
      // 它是与UI完全同步的、最完整的数据。
      const dataToSend = JSON.parse(JSON.stringify(currentCollection.value));

      try {
        if (isEditing.value) {
          // 更新时，发送完整的 dataToSend 对象
          await axios.put(`/api/custom_collections/${dataToSend.id}`, dataToSend);
          message.success('合集更新成功！');
        } else {
          // 创建时，也发送完整的 dataToSend 对象
          await axios.post('/api/custom_collections', dataToSend);
          message.success('合集创建成功！');
        }
        showModal.value = false;
        fetchCollections(); // 重新加载列表以显示最新状态
      } catch (error) {
        message.error(error.response?.data?.error || '保存失败。');
      } finally {
        isSaving.value = false;
      }
    }
  });
};

// --- 表格列定义 (保持不变) ---
const columns = [
  { title: '名称', key: 'name', width: 250 },
  { 
    title: '类型', 
    key: 'type',
    render(row) {
      const tagType = row.type === 'list' ? 'info' : 'default';
      const text = row.type === 'list' ? '榜单导入' : '筛选生成';
      return h(NTag, { type: tagType, bordered: false }, { default: () => text });
    }
  },
  { 
    title: '状态', 
    key: 'status',
    render(row) {
      const tagType = row.status === 'active' ? 'success' : 'warning';
      const text = row.status === 'active' ? '启用' : '暂停';
      return h(NTag, { type: tagType, bordered: false }, { default: () => text });
    }
  },
  { 
    title: '上次同步', 
    key: 'last_synced_at',
    render(row) {
      if (!row.last_synced_at) return '从未';
      try {
        return format(new Date(row.last_synced_at), 'yyyy-MM-dd HH:mm:ss');
      } catch { return '日期无效'; }
    }
  },
  { title: '关联Emby合集ID', key: 'emby_collection_id' },
  {
    title: '操作',
    key: 'actions',
    render(row) {
      return h(NSpace, null, {
        default: () => [
          h(NButton, { size: 'small', type: 'primary', ghost: true, loading: syncLoading.value[row.id], onClick: () => handleSync(row) }, { default: () => '生成', icon: () => h(NIcon, { component: GenerateIcon }) }),
          h(NButton, { size: 'small', onClick: () => handleEditClick(row) }, { default: () => '编辑', icon: () => h(NIcon, { component: EditIcon }) }),
          h(NPopconfirm, { onPositiveClick: () => handleDelete(row) }, {
            trigger: () => h(NButton, { size: 'small', type: 'error', ghost: true }, { default: () => '删除', icon: () => h(NIcon, { component: DeleteIcon }) }),
            default: () => `确定要删除 "${row.name}" 这个自定义合集吗？此操作不可恢复。`
          })
        ]
      });
    }
  }
];

// --- onMounted (保持不变) ---
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
</style>
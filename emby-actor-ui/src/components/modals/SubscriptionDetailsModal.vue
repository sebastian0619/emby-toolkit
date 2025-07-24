<template>
  <n-modal
    :show="props.show"
    @update:show="val => emit('update:show', val)"
    preset="card"
    style="width: 95%; max-width: 1200px;"
    :title="subscriptionData ? `订阅详情 - ${subscriptionData.actor_name}` : '加载中...'"
    :bordered="false"
    size="huge"
  >
    <div v-if="loading" style="text-align: center; padding: 50px 0;"><n-spin size="large" /></div>
    <div v-else-if="error" style="text-align: center; padding: 50px 0;"><n-alert title="加载失败" type="error">{{ error }}</n-alert></div>
    <div v-else-if="subscriptionData">
      <n-tabs type="line" animated default-value="tracking">
        <n-tab-pane name="tracking" tab="追踪列表">
          <n-data-table
            :columns="columns"
            :data="subscriptionData.tracked_media"
            :pagination="{ pageSize: 10 }"
            :bordered="false"
            size="small"
          />
        </n-tab-pane>
        <n-tab-pane name="config" tab="订阅配置">
          <div style="max-width: 600px; margin: 0 auto; padding: 20px 0;">
            <p style="margin-bottom: 20px;">在这里可以修改订阅配置，保存后将对未来的扫描生效。</p>
            <!-- ★★★ 核心修复 1：使用正确的 v-model 绑定 ★★★ -->
            <subscription-config-form v-model="editableConfig" />
            <n-space justify="end" style="margin-top: 20px;">
              <n-button @click="resetConfig">重置更改</n-button>
              <n-button type="primary" @click="saveConfig">保存配置</n-button>
            </n-space>
          </div>
        </n-tab-pane>
      </n-tabs>
    </div>
    <template #footer>
      <n-space justify="space-between">
        <n-space>
        <n-popconfirm @positive-click="handleDelete">
          <template #trigger>
            <n-button type="error" ghost>删除此订阅</n-button>
          </template>
          确定要删除对该演员的订阅吗？所有追踪记录将一并清除。
        </n-popconfirm>
        <n-button
            v-if="subscriptionData"
            :type="subscriptionData.status === 'active' ? 'warning' : 'success'"
            ghost
            @click="handleToggleStatus"
        >
            {{ subscriptionData.status === 'active' ? '暂停订阅' : '恢复订阅' }}
        </n-button>
        </n-space>
        <n-button type="primary" @click="handleRefresh">手动刷新</n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<script setup>
import { ref, watch, h } from 'vue';
import { NModal, NSpin, NAlert, NTabs, NTabPane, NDataTable, NTag, NButton, NSpace, NPopconfirm, useMessage } from 'naive-ui';
import axios from 'axios';
import SubscriptionConfigForm from './SubscriptionConfigForm.vue';

const props = defineProps({
  show: Boolean,
  subscriptionId: Number,
});
const emit = defineEmits(['update:show', 'subscription-updated', 'subscription-deleted']);

const message = useMessage();
const loading = ref(false);
const error = ref(null);
const subscriptionData = ref(null);
const editableConfig = ref({});

// 定义数据表的列
const columns = [
  {
    title: '海报',
    key: 'poster_path',
    render(row) {
      const url = row.poster_path ? `https://image.tmdb.org/t/p/w92${row.poster_path}` : 'https://via.placeholder.com/92x138.png?text=N/A';
      return h('img', { src: url, style: 'width: 45px; height: 67px; object-fit: cover; border-radius: 3px;' });
    }
  },
  { title: '标题', key: 'title', ellipsis: { tooltip: true } },
  { 
    title: '类型', 
    key: 'media_type', 
    width: 80,
    render(row) {
      const typeMap = {
        'Series': '电视剧',
        'Movie': '电影'
      };
      return typeMap[row.media_type] || row.media_type;
    }
  },
  { title: '发行日期', key: 'release_date', width: 120 },
  {
    title: '状态',
    key: 'status',
    render(row) {
      const statusMap = {
        'IN_LIBRARY': { type: 'success', text: '已入库' },
        'SUBSCRIBED': { type: 'info', text: '已订阅' },
        'PENDING_RELEASE': { type: 'default', text: '待发行' },
        'MISSING': { type: 'error', text: '缺失' },
      };
      const info = statusMap[row.status] || { type: 'warning', text: '未知' };
      return h(NTag, { type: info.type, size: 'small', round: true }, { default: () => info.text });
    }
  }
];

const fetchDetails = async (id) => {
  if (!id) return;
  loading.value = true;
  error.value = null;
  subscriptionData.value = null;
  try {
    const response = await axios.get(`/api/actor-subscriptions/${id}`);
    subscriptionData.value = response.data;
    resetConfig();
  } catch (err) {
    error.value = err.response?.data?.error || '加载订阅详情失败。';
  } finally {
    loading.value = false;
  }
};

const resetConfig = () => {
  if (!subscriptionData.value) return;
  editableConfig.value = {
    start_year: subscriptionData.value.config_start_year,
    media_types: (subscriptionData.value.config_media_types || 'Movie,TV').split(','),
    genres_include: JSON.parse(subscriptionData.value.config_genres_include_json || '[]'),
    genres_exclude: JSON.parse(subscriptionData.value.config_genres_exclude_json || '[]'),
    min_rating: subscriptionData.value.config_min_rating || 6.0,
  };
};

const saveConfig = async () => {
  if (!props.subscriptionId) return;
  try {
    const payload = {
      config: {
        start_year: editableConfig.value.start_year,
        media_types: editableConfig.value.media_types.join(','),
        genres_include_json: JSON.stringify(editableConfig.value.genres_include),
        genres_exclude_json: JSON.stringify(editableConfig.value.genres_exclude),
        min_rating: editableConfig.value.min_rating || 0.0,
      }
    };
    await axios.put(`/api/actor-subscriptions/${props.subscriptionId}`, payload);
    message.success('配置已成功保存！下次扫描将使用新配置。');
    emit('subscription-updated');
    // 保存后刷新一下数据，确保显示的是最新的
    fetchDetails(props.subscriptionId);
  } catch (err) {
    message.error(err.response?.data?.error || '保存配置失败。');
  }
};

const handleDelete = async () => {
  if (!props.subscriptionId) return;
  try {
    await axios.delete(`/api/actor-subscriptions/${props.subscriptionId}`);
    message.success('订阅已成功删除！');
    emit('subscription-deleted');
    emit('update:show', false);
  } catch (err) {
    message.error(err.response?.data?.error || '删除订阅失败。');
  }
};

const handleRefresh = async () => {
  if (!props.subscriptionId) return;
  try {
    await axios.post(`/api/actor-subscriptions/${props.subscriptionId}/refresh`);
    message.success('手动刷新任务已提交到后台！请稍后查看任务状态。');
    emit('update:show', false);
  } catch (err) {
    message.error(err.response?.data?.error || '启动刷新任务失败。');
  }
};
const handleToggleStatus = async () => {
  if (!props.subscriptionId || !subscriptionData.value) return;

  const newStatus = subscriptionData.value.status === 'active' ? 'paused' : 'active';
  const actionText = newStatus === 'paused' ? '暂停' : '恢复';

  try {
    // 构造一个符合后端要求的、完整的 payload
    // 我们将新的 status 和当前已保存的 config 一起发送
    const payload = {
      status: newStatus,
      config: {
        start_year: subscriptionData.value.config_start_year,
        media_types: subscriptionData.value.config_media_types,
        genres_include_json: subscriptionData.value.config_genres_include_json,
        genres_exclude_json: subscriptionData.value.config_genres_exclude_json,
      }
    };

    // 发送带有完整 payload 的 PUT 请求
    await axios.put(`/api/actor-subscriptions/${props.subscriptionId}`, payload);

    message.success(`订阅已成功${actionText}！`);
    
    // 通知父组件刷新列表
    emit('subscription-updated');
    
    // 重新获取当前详情以更新模态框内的状态
    await fetchDetails(props.subscriptionId);

  } catch (err) {
    message.error(err.response?.data?.error || `${actionText}订阅失败。`);
  }
};
watch(() => props.subscriptionId, (newId) => {
  if (newId && props.show) {
    fetchDetails(newId);
  }
});

watch(() => props.show, (newVal) => {
  if (newVal && props.subscriptionId) {
    fetchDetails(props.subscriptionId);
  }
});
</script>
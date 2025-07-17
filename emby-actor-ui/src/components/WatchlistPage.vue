<!-- src/components/WatchlistPage.vue (带视图切换的最终版) -->

<template>
  <n-layout content-style="padding: 24px;">
  <div class="watchlist-page">
    <n-page-header>
      <template #title>
        <n-space align="center">
          <span>智能追剧列表</span>
          <!-- ★★★ 计数器现在基于过滤后的列表 ★★★ -->
          <n-tag v-if="filteredWatchlist.length > 0" type="info" round :bordered="false" size="small">
            {{ filteredWatchlist.length }} 部
          </n-tag>
        </n-space>
      </template>
      <template #extra>
        <n-space>
          <!-- ★★★ 新增：视图切换器 ★★★ -->
          <n-radio-group v-model:value="currentView" size="small">
            <n-radio-button value="inProgress">追剧中</n-radio-button>
            <n-radio-button value="completed">已完结</n-radio-button>
          </n-radio-group>

          <n-tooltip>
            <template #trigger>
              <n-button @click="triggerAllWatchlistUpdate" :loading="isBatchUpdating" circle>
                <template #icon><n-icon :component="SyncOutline" /></template>
              </n-button>
            </template>
            立即检查所有在追剧集
          </n-tooltip>
        </n-space>
      </template>
    </n-page-header>
    <n-divider />

    <div v-if="isLoading" class="center-container">
      <n-spin size="large" />
    </div>

    <div v-else-if="error" class="center-container">
      <n-alert title="加载错误" type="error" style="max-width: 500px;">{{ error }}</n-alert>
    </div>

    <!-- ★★★ v-if 条件现在基于过滤后的列表 ★★★ -->
    <div v-else-if="filteredWatchlist.length > 0">
      <!-- ★★★ v-for 现在遍历过滤后的列表 ★★★ -->
      <n-grid cols="1 s:2 m:3 l:4 xl:5" :x-gap="20" :y-gap="20" responsive="screen">
        <n-gi v-for="item in filteredWatchlist" :key="item.item_id">
          
          <n-card class="glass-section" :bordered="false" content-style="display: flex; padding: 0; gap: 16px;">
            
            <!-- 左侧列：海报 -->
            <div class="card-poster-container">
              <n-image
                lazy
                :src="getPosterUrl(item.item_id)"
                class="card-poster"
                object-fit="cover"
              >
                <template #placeholder>
                  <div class="poster-placeholder"><n-icon :component="TvIcon" size="32" /></div>
                </template>
              </n-image>
            </div>

            <!-- 右侧列：信息和操作 -->
            <div class="card-content-container">
              <!-- 顶部：标题和删除按钮 -->
              <div class="card-header">
                <n-ellipsis class="card-title" :tooltip="{ style: { maxWidth: '300px' } }">
                  {{ item.item_name }}
                </n-ellipsis>
                <n-popconfirm @positive-click="() => removeFromWatchlist(item.item_id, item.item_name)">
                  <template #trigger>
                    <n-button text type="error" circle title="移除" size="tiny">
                      <template #icon><n-icon :component="TrashIcon" /></template>
                    </n-button>
                  </template>
                  确定要从追剧列表中移除《{{ item.item_name }}》吗？
                </n-popconfirm>
              </div>

              <!-- 中间：状态和信息 -->
              <div class="card-status-area">
                <n-space align="center">
                  <n-button 
                    round 
                    size="tiny"
                    :type="statusInfo(item.status).type" 
                    @click="() => updateStatus(item.item_id, statusInfo(item.status).next)"
                    :title="`点击切换到 '${statusInfo(item.status).nextText}'`"
                  >
                    <template #icon><n-icon :component="statusInfo(item.status).icon" /></template>
                    {{ statusInfo(item.status).text }}
                  </n-button>
                  <n-text :depth="3" class="last-checked-text">
                    上次检查: {{ formatTimestamp(item.last_checked_at) }}
                  </n-text>
                </n-space>
              </div>

              <!-- 底部：操作按钮 -->
              <div class="card-actions">
                <n-tooltip>
                  <template #trigger>
                    <n-button text @click="() => triggerSingleUpdate(item.item_id)" :disabled="item.status === 'Completed'">

                      <template #icon><n-icon :component="RefreshIcon" size="18" /></template>
                    </n-button>
                  </template>
                  立即检查更新
                </n-tooltip>
                <n-tooltip>
                  <template #trigger>
                    <n-button text tag="a" :href="getEmbyUrl(item.item_id)" target="_blank">
                      <template #icon><n-icon :component="EmbyIcon" size="18" /></template>
                    </n-button>
                  </template>
                  在 Emby 中打开
                </n-tooltip>
                <n-tooltip>
                  <template #trigger>
                    <n-button text tag="a" :href="`https://www.themoviedb.org/tv/${item.tmdb_id}`" target="_blank">
                      <template #icon><n-icon :component="TMDbIcon" size="18" /></template>
                    </n-button>
                  </template>
                  在 TMDb 中打开
                </n-tooltip>
              </div>
            </div>

          </n-card>
        </n-gi>
      </n-grid>
    </div>

    <div v-else class="center-container">
      <!-- ★★★ 空状态描述现在是动态的 ★★★ -->
      <n-empty :description="emptyStateDescription" size="huge" />
    </div>
  </div>
  </n-layout>
</template>

<script setup>
// ★★★ 引入 computed ★★★
import { ref, onMounted, h, computed } from 'vue';
import axios from 'axios';
import { 
  NPageHeader, NDivider, NEmpty, NTag, NButton, NSpace, NIcon, useMessage,
  NPopconfirm, NTooltip, NGrid, NGi, NCard, NImage, NEllipsis, NSpin, NAlert,
  NRadioGroup, NRadioButton // ★★★ 引入新组件
} from 'naive-ui';
import { 
  SyncOutline, TvOutline as TvIcon, TrashOutline as TrashIcon, RefreshOutline as RefreshIcon,
  CheckmarkCircleOutline as WatchingIcon, PauseCircleOutline as PausedIcon, CheckmarkDoneCircleOutline as CompletedIcon
} from '@vicons/ionicons5';
import { format, parseISO } from 'date-fns';
import { useConfig } from '../composables/useConfig.js';

// --- 自定义图标 (保持不变) ---
const EmbyIcon = () => h('svg', { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 48 48", width: "18", height: "18" }, [ h('path', { d: "M24,4.2c-11,0-19.8,8.9-19.8,19.8S13,43.8,24,43.8s19.8-8.9,19.8-19.8S35,4.2,24,4.2z M24,39.8c-8.7,0-15.8-7.1-15.8-15.8S15.3,8.2,24,8.2s15.8,7.1,15.8,15.8S32.7,39.8,24,39.8z", fill: "currentColor" }), h('polygon', { points: "22.2,16.4 22.2,22.2 16.4,22.2 16.4,25.8 22.2,25.8 22.2,31.6 25.8,31.6 25.8,25.8 31.6,25.8 31.6,22.2 25.8,22.2 25.8,16.4 ", fill: "currentColor" }) ]);
const TMDbIcon = () => h('svg', { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 512 512", width: "18", height: "18" }, [ h('path', { d: "M256 512A256 256 0 1 0 256 0a256 256 0 1 0 0 512zM133.2 176.6a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zm63.3-22.4a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm74.8 108.2c-27.5-3.3-50.2-26-53.5-53.5a8 8 0 0 1 16-.6c2.3 19.3 18.8 34 38.1 31.7a8 8 0 0 1 7.4 8c-2.3.3-4.5.4-6.8.4zm-74.8-108.2a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm149.7 22.4a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zM133.2 262.6a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zm63.3-22.4a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm74.8 108.2c-27.5-3.3-50.2-26-53.5-53.5a8 8 0 0 1 16-.6c2.3 19.3 18.8 34 38.1 31.7a8 8 0 0 1 7.4 8c-2.3.3-4.5.4-6.8.4zm-74.8-108.2a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm149.7 22.4a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8z", fill: "#01b4e4" }) ]);

const { configModel } = useConfig();
const message = useMessage();

// --- State Refs ---
const rawWatchlist = ref([]); // ★★★ 存储所有原始数据
const currentView = ref('inProgress'); // ★★★ 控制当前视图
const isLoading = ref(true);
const isBatchUpdating = ref(false);
const error = ref(null);

// ★★★ 新增：计算属性，用于动态过滤列表 ★★★
const filteredWatchlist = computed(() => {
  if (currentView.value === 'inProgress') {
    return rawWatchlist.value.filter(item => item.status === 'Watching' || item.status === 'Paused');
  }
  if (currentView.value === 'completed') {
    return rawWatchlist.value.filter(item => item.status === 'Completed');
  }
  return [];
});

// ★★★ 新增：动态的空状态描述 ★★★
const emptyStateDescription = computed(() => {
  if (currentView.value === 'inProgress') {
    return '追剧列表为空，快去“手动处理”页面搜索并添加你正在追的剧集吧！';
  }
  return '还没有已完结的剧集。';
});

// --- 辅助函数 ---
const formatTimestamp = (timestamp) => {
  if (!timestamp) return '从未';
  try { return format(parseISO(timestamp), 'MM-dd HH:mm'); } 
  catch (e) { return 'N/A'; }
};
const getPosterUrl = (itemId) => `/image_proxy/Items/${itemId}/Images/Primary?maxHeight=360&tag=1`;
const getEmbyUrl = (itemId) => {
  const embyServerUrl = configModel.value?.emby_server_url;
  if (!embyServerUrl) return '#';
  const baseUrl = embyServerUrl.endsWith('/') ? embyServerUrl.slice(0, -1) : embyServerUrl;
  return `${baseUrl}/web/index.html#!/item?id=${itemId}`;
};
const statusInfo = (status) => {
  const map = {
    'Watching': { type: 'success', text: '追剧中', icon: WatchingIcon, next: 'Paused', nextText: '暂停' },
    'Paused': { type: 'warning', text: '已暂停', icon: PausedIcon, next: 'Watching', nextText: '继续追' },
    'Completed': { type: 'default', text: '已完结', icon: CompletedIcon, next: 'Watching', nextText: '重新追' },
  };
  return map[status] || map['Paused'];
};

// --- API 调用逻辑 ---
const fetchWatchlist = async () => {
  isLoading.value = true;
  error.value = null;
  try {
    const response = await axios.get('/api/watchlist');
    // ★★★ 不再过滤，直接存储原始数据 ★★★
    rawWatchlist.value = response.data;
  } catch (err) {
    error.value = err.response?.data?.error || '获取追剧列表失败。';
  } finally {
    isLoading.value = false;
  }
};
const updateStatus = async (itemId, newStatus) => {
  // ★★★ 操作的是原始列表中的项 ★★★
  const item = rawWatchlist.value.find(i => i.item_id === itemId);
  if (!item) return;
  const oldStatus = item.status;
  item.status = newStatus; // 直接修改状态，计算属性会自动响应
  try {
    await axios.post('/api/watchlist/update_status', { item_id: itemId, new_status: newStatus });
    message.success('状态更新成功！');
  } catch (err) {
    item.status = oldStatus;
    message.error(err.response?.data?.error || '更新状态失败。');
  }
};
const removeFromWatchlist = async (itemId, itemName) => {
  try {
    await axios.post(`/api/watchlist/remove/${itemId}`);
    message.success(`已将《${itemName}》从追剧列表移除。`);
    // ★★★ 从原始列表中移除 ★★★
    rawWatchlist.value = rawWatchlist.value.filter(i => i.item_id !== itemId);
  } catch (err)
 {
    message.error(err.response?.data?.error || '移除失败。');
  }
};
const triggerAllWatchlistUpdate = async () => {
  isBatchUpdating.value = true;
  try {
    const response = await axios.post('/api/watchlist/trigger_full_update');
    message.success(response.data.message || '所有追剧项目更新任务已启动！');
  } catch (err) {
    message.error(err.response?.data?.error || '启动更新任务失败。');
  } finally {
    isBatchUpdating.value = false;
  }
};
const triggerSingleUpdate = async (itemId) => {
  message.loading(`正在为该剧集检查更新...`, { duration: 0, key: `updating-${itemId}` });
  try {
    const response = await axios.post(`/api/watchlist/trigger_update/${itemId}/`);
    message.destroyAll();
    message.success(response.data.message || '单项更新任务已启动！');
  } catch (err) {
    message.destroyAll();
    message.error(err.response?.data?.error || '启动单项更新失败。');
  }
};

// --- 生命周期钩子 ---
onMounted(() => {
  fetchWatchlist();
});
</script>

<style scoped>
.watchlist-page { padding: 0 10px; }
.center-container { display: flex; justify-content: center; align-items: center; height: calc(100vh - 200px); }
.watchlist-card { transition: all 0.2s ease-in-out; overflow: hidden; }
.watchlist-card:hover { transform: translateY(-4px); box-shadow: var(--n-box-shadow-hover); }

.card-poster-container {
  flex-shrink: 0;
  width: 120px;
  height: 180px;
}
.card-poster { width: 100%; height: 100%; }
.poster-placeholder { display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; background-color: var(--n-action-color); }

.card-content-container {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  padding: 12px 12px 12px 0;
  min-width: 0;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 8px;
  flex-shrink: 0;
}
.card-title {
  font-weight: 600;
  font-size: 1.1em;
  line-height: 1.3;
}
.card-status-area {
  flex-grow: 1;
  padding-top: 8px;
}
.last-checked-text {
  display: block;
  font-size: 0.8em;
  margin-top: 6px;
}
.card-actions {
  border-top: 1px solid var(--n-border-color);
  padding-top: 8px;
  margin-top: 8px;
  display: flex;
  justify-content: space-around;
  align-items: center;
  flex-shrink: 0;
}
</style>
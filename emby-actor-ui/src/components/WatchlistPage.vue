<!-- src/components/WatchlistPage.vue (最终健壮版 - 弹窗终极修复) -->
<template>
  <n-layout content-style="padding: 24px;">
    <div class="watchlist-page">
      <n-page-header>
        <template #title>
          <n-space align="center">
            <span>智能追剧列表</span>
            <n-tag v-if="filteredWatchlist.length > 0" type="info" round :bordered="false" size="small">
              {{ filteredWatchlist.length }} 部
            </n-tag>
          </n-space>
        </template>
        <template #extra>
          <n-space>
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

      <div v-if="isLoading" class="center-container"><n-spin size="large" /></div>
      <div v-else-if="error" class="center-container"><n-alert title="加载错误" type="error" style="max-width: 500px;">{{ error }}</n-alert></div>
      <div v-else-if="filteredWatchlist.length > 0">
        <n-grid cols="1 s:1 m:2 l:3 xl:4" :x-gap="20" :y-gap="20" responsive="screen">
          <n-gi v-for="item in renderedWatchlist" :key="item.item_id">
            <n-card class="glass-section" :bordered="false" content-style="display: flex; padding: 0; gap: 16px;">
              <div class="card-poster-container">
                <n-image lazy :src="getPosterUrl(item.item_id)" class="card-poster" object-fit="cover">
                  <template #placeholder><div class="poster-placeholder"><n-icon :component="TvIcon" size="32" /></div></template>
                </n-image>
              </div>
              <div class="card-content-container">
                <div class="card-header">
                  <n-ellipsis class="card-title" :tooltip="{ style: { maxWidth: '300px' } }">{{ item.item_name }}</n-ellipsis>
                  <n-popconfirm @positive-click="() => removeFromWatchlist(item.item_id, item.item_name)">
                    <template #trigger><n-button text type="error" circle title="移除" size="tiny"><template #icon><n-icon :component="TrashIcon" /></template></n-button></template>
                    确定要从追剧列表中移除《{{ item.item_name }}》吗？
                  </n-popconfirm>
                </div>
                <div class="card-status-area">
                  <n-space vertical size="small">
                    <n-space align="center">
                      <n-button round size="tiny" :type="statusInfo(item.status).type" @click="() => updateStatus(item.item_id, statusInfo(item.status).next)" :title="`点击切换到 '${statusInfo(item.status).nextText}'`">
                        <template #icon><n-icon :component="statusInfo(item.status).icon" /></template>
                        {{ statusInfo(item.status).text }}
                      </n-button>
                      <n-tag v-if="item.tmdb_status" size="small" :bordered="false" :type="getSmartTMDbStatusType(item)">
                        {{ getSmartTMDbStatusText(item) }}
                      </n-tag>
                      <n-tag v-if="hasMissing(item)" type="warning" size="small" round>{{ getMissingCountText(item) }}</n-tag>
                    </n-space>
                    <n-text v-if="nextEpisode(item)?.name" :depth="3" class="next-episode-text">
                      <n-icon :component="CalendarIcon" /> 待播: {{ nextEpisode(item).name }} ({{ formatAirDate(nextEpisode(item).air_date) }})
                    </n-text>
                    <n-text :depth="3" class="last-checked-text">上次检查: {{ formatTimestamp(item.last_checked_at) }}</n-text>
                  </n-space>
                </div>
                <div class="card-actions">
                  <!-- ✨ [核心修复] 直接传递完整的 item 对象 -->
                  <n-button type="primary" size="small" @click="() => openMissingInfoModal(item)" :disabled="!hasMissing(item)">
                    <template #icon><n-icon :component="EyeIcon" /></template>
                    查看缺失
                  </n-button>
                  <n-tooltip>
                    <template #trigger>
                      <n-button 
                        circle 
                        :loading="refreshingItems[item.item_id]" 
                        @click="() => triggerSingleRefresh(item.item_id, item.item_name)"
                      >
                        <template #icon><n-icon :component="SyncOutline" /></template>
                      </n-button>
                    </template>
                    立即刷新此剧集
                  </n-tooltip>
                  <n-tooltip>
                    <template #trigger><n-button text @click="openInEmby(item.item_id)"><template #icon><n-icon :component="EmbyIcon" size="18" /></template></n-button></template>
                    在 Emby 中打开
                  </n-tooltip>
                  <n-tooltip>
                    <template #trigger><n-button text tag="a" :href="`https://www.themoviedb.org/tv/${item.tmdb_id}`" target="_blank"><template #icon><n-icon :component="TMDbIcon" size="18" /></template></n-button></template>
                    在 TMDb 中打开
                  </n-tooltip>
                </div>
              </div>
            </n-card>
          </n-gi>
        </n-grid>

        <div ref="loaderRef" class="loader-trigger">
          <n-spin v-if="hasMore" size="small" />
        </div>

      </div>
      <div v-else class="center-container"><n-empty :description="emptyStateDescription" size="huge" /></div>
    </div>

    <n-modal v-model:show="showModal" preset="card" style="width: 90%; max-width: 900px;" :title="selectedSeries ? `缺失详情 - ${selectedSeries.item_name}` : ''" :bordered="false" size="huge">
      <div v-if="selectedSeries && missingData">
        <n-tabs type="line" animated>
          <n-tab-pane name="seasons" :tab="`缺失的季 (${missingData.missing_seasons.length})`" :disabled="missingData.missing_seasons.length === 0">
            <n-list bordered>
              <n-list-item v-for="season in missingData.missing_seasons" :key="season.season_number">
                <template #prefix><n-tag type="warning">S{{ season.season_number }}</n-tag></template>
                <n-ellipsis>{{ season.name }} ({{ season.episode_count }}集, {{ formatAirDate(season.air_date) }})</n-ellipsis>
                <template #suffix><n-button size="small" type="primary" @click="subscribeSeason(season.season_number)" :loading="subscribing['s'+season.season_number]">订阅本季</n-button></template>
              </n-list-item>
            </n-list>
          </n-tab-pane>
          <n-tab-pane name="episodes" :tab="`缺失的集 (${missingData.missing_episodes.length})`" :disabled="missingData.missing_episodes.length === 0">
            <n-list bordered>
              <n-list-item v-for="ep in missingData.missing_episodes" :key="`${ep.season_number}-${ep.episode_number}`">
                <template #prefix><n-tag>S{{ ep.season_number.toString().padStart(2, '0') }}E{{ ep.episode_number.toString().padStart(2, '0') }}</n-tag></template>
                <n-ellipsis>{{ ep.title }} ({{ formatAirDate(ep.air_date) }})</n-ellipsis>
              </n-list-item>
            </n-list>
          </n-tab-pane>
        </n-tabs>
      </div>
    </n-modal>
  </n-layout>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, h, computed, watch } from 'vue';
import axios from 'axios';
import { NLayout, NPageHeader, NDivider, NEmpty, NTag, NButton, NSpace, NIcon, useMessage, NPopconfirm, NTooltip, NGrid, NGi, NCard, NImage, NEllipsis, NSpin, NAlert, NRadioGroup, NRadioButton, NModal, NTabs, NTabPane, NList, NListItem } from 'naive-ui';
import { SyncOutline, TvOutline as TvIcon, TrashOutline as TrashIcon, EyeOutline as EyeIcon, CalendarOutline as CalendarIcon, CheckmarkCircleOutline as WatchingIcon, PauseCircleOutline as PausedIcon, CheckmarkDoneCircleOutline as CompletedIcon } from '@vicons/ionicons5';
import { format, parseISO } from 'date-fns';
import { useConfig } from '../composables/useConfig.js';

const EmbyIcon = () => h('svg', { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 48 48", width: "18", height: "18" }, [ h('path', { d: "M24,4.2c-11,0-19.8,8.9-19.8,19.8S13,43.8,24,43.8s19.8-8.9,19.8-19.8S35,4.2,24,4.2z M24,39.8c-8.7,0-15.8-7.1-15.8-15.8S15.3,8.2,24,8.2s15.8,7.1,15.8,15.8S32.7,39.8,24,39.8z", fill: "currentColor" }), h('polygon', { points: "22.2,16.4 22.2,22.2 16.4,22.2 16.4,25.8 22.2,25.8 22.2,31.6 25.8,31.6 25.8,25.8 31.6,31.6 31.6,22.2 25.8,22.2 25.8,16.4 ", fill: "currentColor" }) ]);
const TMDbIcon = () => h('svg', { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 512 512", width: "18", height: "18" }, [ h('path', { d: "M256 512A256 256 0 1 0 256 0a256 256 0 1 0 0 512zM133.2 176.6a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zm63.3-22.4a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm74.8 108.2c-27.5-3.3-50.2-26-53.5-53.5a8 8 0 0 1 16-.6c2.3 19.3 18.8 34 38.1 31.7a8 8 0 0 1 7.4 8c-2.3.3-4.5.4-6.8.4zm-74.8-108.2a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm149.7 22.4a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zM133.2 262.6a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zm63.3-22.4a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm74.8 108.2c-27.5-3.3-50.2-26-53.5-53.5a8 8 0 0 1 16-.6c2.3 19.3 18.8 34 38.1 31.7a8 8 0 0 1 7.4 8c-2.3.3-4.5.4-6.8.4zm-74.8-108.2a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm149.7 22.4a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8z", fill: "#01b4e4" }) ]);

const { configModel } = useConfig();
const message = useMessage();

const rawWatchlist = ref([]);
const currentView = ref('inProgress');
const isLoading = ref(true);
const isBatchUpdating = ref(false);
const error = ref(null);
const showModal = ref(false);
// ✨ [核心修复] 直接存储被点击的对象
const selectedSeries = ref(null);
const subscribing = ref({});
const refreshingItems = ref({});

const displayCount = ref(30);
const INCREMENT = 30;
const loaderRef = ref(null);
let observer = null;

const triggerSingleRefresh = async (itemId, itemName) => {
  refreshingItems.value[itemId] = true;
  try {
    await axios.post(`/api/watchlist/refresh/${itemId}`);
    message.success(`《${itemName}》的刷新任务已提交！`);
    setTimeout(() => { fetchWatchlist(); }, 5000);
  } catch (err) {
    message.error(err.response?.data?.error || '启动刷新失败。');
  } finally {
    setTimeout(() => { refreshingItems.value[itemId] = false; }, 5000);
  }
};
const subscribeSeason = async (seasonNumber) => {
  if (!selectedSeries.value) return;
  const key = `s${seasonNumber}`;
  subscribing.value[key] = true;
  try {
    await axios.post('/api/subscribe/moviepilot/series', {
      tmdb_id: selectedSeries.value.tmdb_id,
      title: selectedSeries.value.item_name,
      season_number: seasonNumber
    });
    message.success(`《${selectedSeries.value.item_name}》第 ${seasonNumber} 季已提交订阅！`);
    if (selectedSeries.value.missing_info_json) {
        const data = JSON.parse(selectedSeries.value.missing_info_json);
        data.missing_seasons = data.missing_seasons.filter(s => s.season_number !== seasonNumber);
        selectedSeries.value.missing_info_json = JSON.stringify(data);
    }
  } catch (err) {
    message.error(err.response?.data?.error || '订阅失败');
  } finally {
    subscribing.value[key] = false;
  }
};

const filteredWatchlist = computed(() => {
  if (currentView.value === 'inProgress') {
    return rawWatchlist.value.filter(item => item.status === 'Watching' || item.status === 'Paused');
  }
  if (currentView.value === 'completed') {
    return rawWatchlist.value.filter(item => item.status === 'Completed');
  }
  return [];
});

const renderedWatchlist = computed(() => {
  return filteredWatchlist.value.slice(0, displayCount.value);
});

const hasMore = computed(() => {
  return displayCount.value < filteredWatchlist.value.length;
});

const loadMore = () => {
  if (hasMore.value) {
    displayCount.value += INCREMENT;
  }
};

watch(currentView, () => {
  displayCount.value = 30;
});

const emptyStateDescription = computed(() => {
  if (currentView.value === 'inProgress') {
    return '追剧列表为空，快去“手动处理”页面搜索并添加你正在追的剧集吧！';
  }
  return '还没有已完结的剧集。';
});

const missingData = computed(() => {
  if (!selectedSeries.value || !selectedSeries.value.missing_info_json) {
    return { missing_seasons: [], missing_episodes: [] };
  }
  try {
    return JSON.parse(selectedSeries.value.missing_info_json);
  } catch (e) {
    return { missing_seasons: [], missing_episodes: [] };
  }
});

const nextEpisode = (item) => {
  if (!item.next_episode_to_air_json) return null;
  try { return JSON.parse(item.next_episode_to_air_json); } 
  catch (e) { return null; }
};

const hasMissing = (item) => {
  if (!item.missing_info_json) return false;
  try {
    const data = JSON.parse(item.missing_info_json);
    return (data.missing_seasons?.length > 0) || (data.missing_episodes?.length > 0);
  } catch (e) {
    return false;
  }
};

const getMissingCountText = (item) => {
  if (!hasMissing(item)) return '';
  const data = JSON.parse(item.missing_info_json);
  const season_count = data.missing_seasons?.length || 0;
  const episode_count = data.missing_episodes?.length || 0;
  let parts = [];
  if (season_count > 0) parts.push(`缺 ${season_count} 季`);
  if (episode_count > 0) parts.push(`缺 ${episode_count} 集`);
  return parts.join(' | ');
};

const formatTimestamp = (timestamp) => {
  if (!timestamp) return '从未';
  try { return format(parseISO(timestamp), 'MM-dd HH:mm'); } 
  catch (e) { return 'N/A'; }
};
const formatAirDate = (dateString) => {
  if (!dateString) return '待定';
  try { return format(parseISO(dateString), 'yyyy-MM-dd'); }
  catch (e) { return 'N/A'; }
};
const getPosterUrl = (itemId) => `/image_proxy/Items/${itemId}/Images/Primary?maxHeight=480&tag=1`;
const getEmbyUrl = (itemId) => {
  const embyServerUrl = configModel.value?.emby_server_url;
  const serverId = configModel.value?.emby_server_id;
  if (!embyServerUrl || !itemId) return '#';
  const baseUrl = embyServerUrl.endsWith('/') ? embyServerUrl.slice(0, -1) : embyServerUrl;
  let finalUrl = `${baseUrl}/web/index.html#!/item?id=${itemId}`;
  if (serverId) {
    finalUrl += `&serverId=${serverId}`;
  }
  return finalUrl;
};
const openInEmby = (itemId) => {
  const url = getEmbyUrl(itemId);
  if (url !== '#') {
    window.open(url, '_blank');
  }
};
const statusInfo = (status) => {
  const map = {
    'Watching': { type: 'success', text: '追剧中', icon: WatchingIcon, next: 'Paused', nextText: '暂停' },
    'Paused': { type: 'warning', text: '已暂停', icon: PausedIcon, next: 'Watching', nextText: '继续追' },
    'Completed': { type: 'default', text: '已完结', icon: CompletedIcon, next: 'Watching', nextText: '重新追' },
  };
  return map[status] || map['Paused'];
};
const translateTmdbStatus = (status) => {
  const statusMap = {
    "Returning Series": "连载中",
    "Ended": "已完结",
    "Canceled": "已取消",
    "In Production": "制作中",
    "Planned": "计划中",
    "Pilot": "试播"
  };
  return statusMap[status] || status;
};
const getSmartTMDbStatusText = (item) => {
  const internalStatus = item.status;
  const tmdbStatus = item.tmdb_status;

  // 只有当内部状态是“已完结”时，才进行特殊判断
  if (internalStatus === 'Completed') {
    // 如果TMDb状态也是完结或取消，则显示“待回归”
    if (tmdbStatus === 'Ended' || tmdbStatus === 'Canceled') {
      return '待回归';
    }
  }
  // 其他所有情况，都正常翻译TMDb状态
  return translateTmdbStatus(tmdbStatus);
};

const getSmartTMDbStatusType = (item) => {
  // 让“待回归”标签显示为蓝色 (info)，以示区别
  if (getSmartTMDbStatusText(item) === '待回归') {
    return 'info';
  }
  // 其他情况使用默认样式
  return 'default';
};
const fetchWatchlist = async () => {
  isLoading.value = true;
  error.value = null;
  try {
    const response = await axios.get('/api/watchlist');
    rawWatchlist.value = response.data;
    displayCount.value = 30;
  } catch (err) {
    error.value = err.response?.data?.error || '获取追剧列表失败。';
  } finally {
    isLoading.value = false;
  }
};
const updateStatus = async (itemId, newStatus) => {
  const item = rawWatchlist.value.find(i => i.item_id === itemId);
  if (!item) return;
  const oldStatus = item.status;
  item.status = newStatus;
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
    rawWatchlist.value = rawWatchlist.value.filter(i => i.item_id !== itemId);
  } catch (err)
 {
    message.error(err.response?.data?.error || '移除失败。');
  }
};
const triggerAllWatchlistUpdate = async () => {
  isBatchUpdating.value = true;
  try {
    const response = await axios.post('/api/tasks/trigger/process-watchlist');
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

// ✨ [核心修复] 函数现在接收完整的 item 对象
const openMissingInfoModal = (item) => {
  selectedSeries.value = item;
  showModal.value = true;
};

onMounted(() => {
  fetchWatchlist();
  observer = new IntersectionObserver(
    (entries) => {
      if (entries[0].isIntersecting) {
        loadMore();
      }
    },
    { threshold: 1.0 }
  );
  if (loaderRef.value) {
    observer.observe(loaderRef.value);
  }
});

onBeforeUnmount(() => {
  if (observer) {
    observer.disconnect();
  }
});

watch(loaderRef, (newEl) => {
  if (observer && newEl) {
    observer.observe(newEl);
  }
});

</script>

<style scoped>
.watchlist-page { padding: 0 10px; }
.center-container { display: flex; justify-content: center; align-items: center; height: calc(100vh - 200px); }
.card-poster-container { flex-shrink: 0; width: 160px; height: 240px; }
.card-poster { width: 100%; height: 100%; }
.poster-placeholder { display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; background-color: var(--n-action-color); }
.card-content-container { flex-grow: 1; display: flex; flex-direction: column; padding: 12px 12px 12px 0; min-width: 0; }
.card-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; flex-shrink: 0; }
.card-title { font-weight: 600; font-size: 1.1em; line-height: 1.3; }
.card-status-area { flex-grow: 1; padding-top: 8px; }
.last-checked-text { display: block; font-size: 0.8em; margin-top: 6px; }
.next-episode-text { display: flex; align-items: center; gap: 4px; font-size: 0.8em; }
.card-actions { border-top: 1px solid var(--n-border-color); padding-top: 8px; margin-top: 8px; display: flex; justify-content: space-around; align-items: center; flex-shrink: 0; }
.loader-trigger {
  height: 50px;
  display: flex;
  justify-content: center;
  align-items: center;
}
</style>
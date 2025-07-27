<!-- src/components/CollectionsPage.vue (最终毕业版 - 弹窗终极修复) -->
<template>
  <n-layout content-style="padding: 24px;">
    <div class="collections-page">
      <n-page-header>
        <template #title>
          电影合集检查
        </template>
        <template #footer>
          <n-space align="center" size="large">
            <n-tag :bordered="false" round>
              共 {{ globalStats.totalCollections }} 合集
            </n-tag>
            <n-tag v-if="globalStats.totalMissingMovies > 0" type="warning" :bordered="false" round>
              {{ globalStats.collectionsWithMissing }} 合集缺失 {{ globalStats.totalMissingMovies }} 部
            </n-tag>
            <n-tag v-if="globalStats.totalUnreleased > 0" type="info" :bordered="false" round>
              {{ globalStats.totalUnreleased }} 部未上映
            </n-tag>
            <n-tag v-if="globalStats.totalSubscribed > 0" type="default" :bordered="false" round>
              {{ globalStats.totalSubscribed }} 部已订阅
            </n-tag>
            <n-tag v-if="globalStats.totalMissingMovies === 0 && globalStats.totalCollections > 0" type="success" :bordered="false" round>
              所有合集均无缺失
            </n-tag>
          </n-space>
        </template>
        <template #extra>
          <n-space>
            <!-- ★★★ [核心修改] "一键忽略" 改为 "一键订阅" ★★★ -->
            <n-popconfirm @positive-click="subscribeAllMissingMovies" :disabled="globalStats.totalMissingMovies === 0">
              <template #trigger>
                <n-tooltip>
                  <template #trigger>
                    <n-button circle :loading="isSubscribingAll" :disabled="globalStats.totalMissingMovies === 0">
                      <template #icon><n-icon :component="CloudDownloadIcon" /></template>
                    </n-button>
                  </template>
                  一键订阅所有缺失
                </n-tooltip>
              </template>
              确定要将所有 {{ globalStats.totalMissingMovies }} 部缺失的电影提交到 MoviePilot 订阅吗？
            </n-popconfirm>

            <n-tooltip>
              <template #trigger>
                <n-button @click="triggerFullRefresh" :loading="isRefreshing" circle>
                  <template #icon><n-icon :component="SyncOutline" /></template>
                </n-button>
              </template>
              刷新所有合集信息
            </n-tooltip>
          </n-space>
        </template>
      </n-page-header>

      <div v-if="isInitialLoading" class="center-container"><n-spin size="large" /></div>
      <div v-else-if="error" class="center-container"><n-alert title="加载错误" type="error" style="max-width: 500px;">{{ error }}</n-alert></div>
      
      <div v-else-if="collections.length > 0" style="margin-top: 24px;">
        <n-grid cols="1 s:2 m:3 l:4 xl:5" :x-gap="20" :y-gap="20" responsive="screen">
          <n-gi v-for="item in renderedCollections" :key="item.emby_collection_id">
            <n-card class="glass-section" :bordered="false" content-style="display: flex; padding: 0; gap: 16px;">
              <div class="card-poster-container"><n-image lazy :src="getCollectionPosterUrl(item.poster_path)" class="card-poster" object-fit="cover"><template #placeholder><div class="poster-placeholder"><n-icon :component="AlbumsIcon" size="32" /></div></template></n-image></div>
              <div class="card-content-container">
                <div class="card-header"><n-ellipsis class="card-title" :tooltip="{ style: { maxWidth: '300px' } }">{{ item.name }}</n-ellipsis></div>
                <div class="card-status-area">
                  <n-space align="center">
                    <n-tooltip :disabled="!isTooltipNeeded(item)">
                      <template #trigger>
                        <n-tag :type="getStatusTagType(item)" round>
                          {{ getShortStatusText(item) }}
                        </n-tag>
                      </template>
                      {{ getFullStatusText(item) }}
                    </n-tooltip>
                    <n-text :depth="3" class="last-checked-text">上次检查: {{ formatTimestamp(item.last_checked_at) }}</n-text>
                  </n-space>
                </div>
                <div class="card-actions">
                  <n-button type="primary" size="small" @click="() => openMissingMoviesModal(item)"><template #icon><n-icon :component="EyeIcon" /></template>查看详情</n-button>
                  <n-tooltip><template #trigger><n-button text @click="openInEmby(item.emby_collection_id)"><template #icon><n-icon :component="EmbyIcon" size="18" /></template></n-button></template>在 Emby 中打开</n-tooltip>
                  <n-tooltip><template #trigger><n-button text tag="a" :href="`https://www.themoviedb.org/collection/${item.tmdb_collection_id}`" target="_blank" :disabled="!item.tmdb_collection_id"><template #icon><n-icon :component="TMDbIcon" size="18" /></template></n-button></template>在 TMDb 中打开</n-tooltip>
                </div>
              </div>
            </n-card>
          </n-gi>
        </n-grid>

        <div ref="loaderRef" class="loader-trigger">
          <n-spin v-if="hasMore" size="small" />
        </div>

      </div>
      <div v-else class="center-container"><n-empty description="没有找到任何电影合集。" size="huge" /></div>
    </div>

    <n-modal v-model:show="showModal" preset="card" style="width: 90%; max-width: 1200px;" :title="selectedCollection ? `详情 - ${selectedCollection.name}` : ''" :bordered="false" size="huge">
      <div v-if="selectedCollection">
        <n-tabs type="line" animated>
          <!-- 缺失影片 -->
          <n-tab-pane name="missing" :tab="`缺失影片 (${missingMoviesInModal.length})`">
            <n-empty v-if="missingMoviesInModal.length === 0" description="太棒了！没有已上映的缺失影片。" style="margin-top: 40px;"></n-empty>
            <n-grid v-else cols="2 s:3 m:4 l:5 xl:6" :x-gap="16" :y-gap="16" responsive="screen">
              <n-gi v-for="movie in missingMoviesInModal" :key="movie.tmdb_id">
                <n-card class="movie-card" content-style="padding: 0;">
                  <template #cover><img :src="getTmdbImageUrl(movie.poster_path)" class="movie-poster" /></template>
                  <div class="movie-info"><div class="movie-title">{{ movie.title }}<br />({{ extractYear(movie.release_date) || '未知年份' }})</div></div>
                  <template #action>
                    <n-button @click="subscribeMovie(movie)" type="primary" size="small" block :loading="subscribing[movie.tmdb_id]">
                      <template #icon><n-icon :component="CloudDownloadIcon" /></template>
                      订阅
                    </n-button>
                  </template>
                </n-card>
              </n-gi>
            </n-grid>
          </n-tab-pane>
          
          <!-- ★★★ [核心修改] 新增 "已入库" 标签页 ★★★ -->
          <n-tab-pane name="in_library" :tab="`已入库 (${inLibraryMoviesInModal.length})`">
             <n-empty v-if="inLibraryMoviesInModal.length === 0" description="该合集在媒体库中没有任何影片。" style="margin-top: 40px;"></n-empty>
            <n-grid v-else cols="2 s:3 m:4 l:5 xl:6" :x-gap="16" :y-gap="16" responsive="screen">
              <n-gi v-for="movie in inLibraryMoviesInModal" :key="movie.tmdb_id">
                <n-card class="movie-card" content-style="padding: 0;">
                  <template #cover><img :src="getTmdbImageUrl(movie.poster_path)" class="movie-poster" /></template>
                  <div class="movie-info"><div class="movie-title">{{ movie.title }}<br />({{ extractYear(movie.release_date) || '未知年份' }})</div></div>
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

          <!-- 未上映 -->
          <n-tab-pane name="unreleased" :tab="`未上映 (${unreleasedMoviesInModal.length})`">
            <n-empty v-if="unreleasedMoviesInModal.length === 0" description="该合集没有已知的未上映影片。" style="margin-top: 40px;"></n-empty>
            <n-grid v-else cols="2 s:3 m:4 l:5 xl:6" :x-gap="16" :y-gap="16" responsive="screen">
              <n-gi v-for="movie in unreleasedMoviesInModal" :key="movie.tmdb_id">
                <n-card class="movie-card" content-style="padding: 0;">
                  <template #cover><img :src="getTmdbImageUrl(movie.poster_path)" class="movie-poster"></template>
                  <div class="movie-info"><div class="movie-title">{{ movie.title }}<br />({{ extractYear(movie.release_date) || '未知年份' }})</div></div>
                </n-card>
              </n-gi>
            </n-grid>
          </n-tab-pane>

          <!-- ★★★ [核心修改] "已忽略" 改为 "已订阅" ★★★ -->
          <n-tab-pane name="subscribed" :tab="`已订阅 (${subscribedMoviesInModal.length})`">
            <n-empty v-if="subscribedMoviesInModal.length === 0" description="你没有订阅此合集中的任何影片。" style="margin-top: 40px;"></n-empty>
            <n-grid v-else cols="2 s:3 m:4 l:5 xl:6" :x-gap="16" :y-gap="16" responsive="screen">
              <n-gi v-for="movie in subscribedMoviesInModal" :key="movie.tmdb_id">
                <n-card class="movie-card" content-style="padding: 0;">
                  <template #cover><img :src="getTmdbImageUrl(movie.poster_path)" class="movie-poster" /></template>
                  <div class="movie-info"><div class="movie-title">{{ movie.title }}<br />({{ extractYear(movie.release_date) || '未知年份' }})</div></div>
                  <template #action>
                    <n-button @click="unsubscribeMovie(movie)" type="warning" size="small" block ghost>
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
import { ref, onMounted, onBeforeUnmount, computed, watch, h } from 'vue';
import axios from 'axios';
import { NLayout, NPageHeader, NEmpty, NTag, NButton, NSpace, NIcon, useMessage, NTooltip, NGrid, NGi, NCard, NImage, NEllipsis, NSpin, NAlert, NModal, NTabs, NTabPane, NPopconfirm } from 'naive-ui';
import { SyncOutline, AlbumsOutline as AlbumsIcon, EyeOutline as EyeIcon, CloudDownloadOutline as CloudDownloadIcon, CloseCircleOutline as CloseCircleIcon, CheckmarkCircleOutline as CheckmarkCircle } from '@vicons/ionicons5';
import { format } from 'date-fns';
import { useConfig } from '../composables/useConfig.js';

const props = defineProps({ taskStatus: { type: Object, required: true } });
const { configModel } = useConfig();
const message = useMessage();

const EmbyIcon = () => h('svg', { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 48 48", width: "18", height: "18" }, [ h('path', { d: "M24,4.2c-11,0-19.8,8.9-19.8,19.8S13,43.8,24,43.8s19.8-8.9,19.8-19.8S35,4.2,24,4.2z M24,39.8c-8.7,0-15.8-7.1-15.8-15.8S15.3,8.2,24,8.2s15.8,7.1,15.8,15.8S32.7,39.8,24,39.8z", fill: "currentColor" }), h('polygon', { points: "22.2,16.4 22.2,22.2 16.4,22.2 16.4,25.8 22.2,25.8 22.2,31.6 25.8,31.6 25.8,25.8 31.6,31.6 31.6,22.2 25.8,22.2 25.8,16.4 ", fill: "currentColor" }) ]);
const TMDbIcon = () => h('svg', { xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 512 512", width: "18", height: "18" }, [ h('path', { d: "M256 512A256 256 0 1 0 256 0a256 256 0 1 0 0 512zM133.2 176.6a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zm63.3-22.4a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm74.8 108.2c-27.5-3.3-50.2-26-53.5-53.5a8 8 0 0 1 16-.6c2.3 19.3 18.8 34 38.1 31.7a8 8 0 0 1 7.4 8c-2.3.3-4.5.4-6.8.4zm-74.8-108.2a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm149.7 22.4a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zM133.2 262.6a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zm63.3-22.4a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm74.8 108.2c-27.5-3.3-50.2-26-53.5-53.5a8 8 0 0 1 16-.6c2.3 19.3 18.8 34 38.1 31.7a8 8 0 0 1 7.4 8c-2.3.3-4.5.4-6.8.4zm-74.8-108.2a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm149.7 22.4a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8z", fill: "#01b4e4" }) ]);

const collections = ref([]);
const isInitialLoading = ref(true);
const isRefreshing = ref(false);
const error = ref(null);
const subscribing = ref({});
const showModal = ref(false);
const selectedCollection = ref(null);
const isSubscribingAll = ref(false);
const displayCount = ref(50);
const INCREMENT = 50;
const loaderRef = ref(null);
let observer = null;

const getMissingCount = (collection) => {
  if (!collection || !Array.isArray(collection.missing_movies)) return 0;
  return collection.missing_movies.filter(m => m.status === 'missing').length;
};

const globalStats = computed(() => {
  const stats = {
    totalCollections: collections.value.length,
    collectionsWithMissing: 0,
    totalMissingMovies: 0,
    totalUnreleased: 0,
    totalSubscribed: 0,
  };

  for (const collection of collections.value) {
    if (Array.isArray(collection.missing_movies)) {
      const missingCount = collection.missing_movies.filter(m => m.status === 'missing').length;
      if (missingCount > 0) {
        stats.collectionsWithMissing++;
        stats.totalMissingMovies += missingCount;
      }
      stats.totalUnreleased += collection.missing_movies.filter(m => m.status === 'unreleased').length;
      stats.totalSubscribed += collection.missing_movies.filter(m => m.status === 'subscribed').length;
    }
  }
  return stats;
});

const sortedCollections = computed(() => {
  return [...collections.value].sort((a, b) => {
    const missingCountA = getMissingCount(a);
    const missingCountB = getMissingCount(b);
    if (missingCountB !== missingCountA) {
      return missingCountB - missingCountA;
    }
    return a.name.localeCompare(b.name);
  });
});

const renderedCollections = computed(() => {
  return sortedCollections.value.slice(0, displayCount.value);
});

const hasMore = computed(() => {
  return displayCount.value < sortedCollections.value.length;
});

const loadMore = () => {
  if (hasMore.value) {
    displayCount.value += INCREMENT;
  }
};

// ★★★ [核心修改] 新的计算属性，用于分离不同状态的电影 ★★★
const inLibraryMoviesInModal = computed(() => {
  if (!selectedCollection.value || !Array.isArray(selectedCollection.value.missing_movies)) return [];
  return selectedCollection.value.missing_movies.filter(movie => movie.status === 'in_library');
});
const missingMoviesInModal = computed(() => {
  if (!selectedCollection.value || !Array.isArray(selectedCollection.value.missing_movies)) return [];
  return selectedCollection.value.missing_movies.filter(movie => movie.status === 'missing');
});
const unreleasedMoviesInModal = computed(() => {
  if (!selectedCollection.value || !Array.isArray(selectedCollection.value.missing_movies)) return [];
  return selectedCollection.value.missing_movies.filter(movie => movie.status === 'unreleased');
});
const subscribedMoviesInModal = computed(() => {
  if (!selectedCollection.value || !Array.isArray(selectedCollection.value.missing_movies)) return [];
  return selectedCollection.value.missing_movies.filter(movie => movie.status === 'subscribed');
});

const loadCachedData = async () => {
  if (collections.value.length === 0) isInitialLoading.value = true;
  error.value = null;
  try {
    const response = await axios.get('/api/collections/status', {
      headers: { 'Cache-Control': 'no-cache', 'Pragma': 'no-cache', 'Expires': '0' },
    });
    collections.value = response.data;
    displayCount.value = 50;
  } catch (err) {
    error.value = err.response?.data?.error || '无法加载合集数据。';
  } finally {
    isInitialLoading.value = false;
  }
};

// ★★★ [核心修改] 调用新的 "一键订阅" API ★★★
const subscribeAllMissingMovies = async () => {
  isSubscribingAll.value = true;
  try {
    const response = await axios.post('/api/collections/subscribe_all_missing');
    message.success(response.data.message || '操作成功！');
    await loadCachedData();
  } catch (err) {
    message.error(err.response?.data?.error || '一键订阅操作失败。');
  } finally {
    isSubscribingAll.value = false;
  }
};

const triggerFullRefresh = async () => {
  isRefreshing.value = true;
  try {
    const response = await axios.post('/api/tasks/trigger/refresh-collections');
    message.success(response.data.message || '刷新任务已在后台启动！');
  } catch (err) {
    message.error(err.response?.data?.error || '启动刷新任务失败。');
  } finally {
    isRefreshing.value = false;
  }
};

onMounted(() => {
  loadCachedData();
  observer = new IntersectionObserver(
    (entries) => { if (entries[0].isIntersecting) { loadMore(); } },
    { threshold: 1.0 }
  );
  if (loaderRef.value) { observer.observe(loaderRef.value); }
});

onBeforeUnmount(() => { if (observer) { observer.disconnect(); } });
watch(loaderRef, (newEl) => { if (observer && newEl) { observer.observe(newEl); } });
watch(() => props.taskStatus.is_running, (isRunning, wasRunning) => {
  if (wasRunning && !isRunning && props.taskStatus.current_action.includes('合集')) {
    message.info('后台合集任务已结束，正在刷新数据...');
    loadCachedData();
  }
});

const openMissingMoviesModal = (collection) => {
  selectedCollection.value = collection;
  showModal.value = true;
};

// ★★★ [核心修改] 统一的状态更新函数 ★★★
const updateMovieStatus = async (movie, newStatus) => {
  try {
    await axios.post('/api/collections/update_movie_status', {
      collection_id: selectedCollection.value.emby_collection_id,
      movie_tmdb_id: movie.tmdb_id,
      new_status: newStatus
    });
    // 直接在本地更新状态，实现动态UI变化
    movie.status = newStatus;
    message.success(`操作成功！`);
  } catch (err) {
    message.error(err.response?.data?.error || '操作失败');
  }
};

// ★★★ [核心修改] 单个订阅现在调用通用函数和 MoviePilot API ★★★
const subscribeMovie = async (movie) => {
  subscribing.value[movie.tmdb_id] = true;
  try {
    // 先提交到 MoviePilot
    await axios.post('/api/subscribe/moviepilot', { tmdb_id: movie.tmdb_id, title: movie.title });
    message.success(`《${movie.title}》已提交订阅`);
    // MoviePilot 成功后，再更新我们自己的数据库状态
    await updateMovieStatus(movie, 'subscribed');
  } catch (err) {
    message.error(err.response?.data?.error || '订阅失败');
  } finally {
    subscribing.value[movie.tmdb_id] = false;
  }
};

// ★★★ [核心修改] 新增取消订阅函数 ★★★
const unsubscribeMovie = (movie) => {
  updateMovieStatus(movie, 'missing');
};

const getEmbyUrl = (itemId) => {
  const embyServerUrl = configModel.value?.emby_server_url;
  const serverId = configModel.value?.emby_server_id;
  if (!embyServerUrl || !itemId) return '#';
  const baseUrl = embyServerUrl.endsWith('/') ? embyServerUrl.slice(0, -1) : embyServerUrl;
  let finalUrl = `${baseUrl}/web/index.html#!/item?id=${itemId}`;
  if (serverId) { finalUrl += `&serverId=${serverId}`; }
  return finalUrl;
};

const openInEmby = (itemId) => {
  const url = getEmbyUrl(itemId);
  if (url !== '#') { window.open(url, '_blank'); }
};

const formatTimestamp = (timestamp) => {
  if (!timestamp) return '从未';
  try { return format(new Date(timestamp * 1000), 'MM-dd HH:mm'); } 
  catch (e) { return 'N/A'; }
};
const getCollectionPosterUrl = (posterPath) => posterPath ? `/image_proxy${posterPath}` : '/img/poster-placeholder.png';
const getTmdbImageUrl = (posterPath) => posterPath ? `https://image.tmdb.org/t/p/w300${posterPath}` : '/img/poster-placeholder.png';

const getStatusTagType = (collection) => {
  if (collection.status === 'unlinked' || collection.status === 'tmdb_error') return 'error';
  if (getMissingCount(collection) > 0) return 'warning';
  return 'success';
};

const getFullStatusText = (collection) => {
  if (collection.status === 'unlinked') return '未关联TMDb';
  if (collection.status === 'tmdb_error') return 'TMDb错误';
  const missingCount = getMissingCount(collection);
  if (missingCount > 0) { return `缺失 ${missingCount} 部`; }
  const parts = [];
  const inLibraryCount = collection.in_library_count || 0;
  if (inLibraryCount > 0) { parts.push(`完整 ${inLibraryCount} 部`); }
  if (Array.isArray(collection.missing_movies)) {
      const unreleasedCount = collection.missing_movies.filter(m => m.status === 'unreleased').length;
      const subscribedCount = collection.missing_movies.filter(m => m.status === 'subscribed').length;
      if (unreleasedCount > 0) { parts.push(`未上映 ${unreleasedCount} 部`); }
      if (subscribedCount > 0) { parts.push(`已订阅 ${subscribedCount} 部`); }
  }
  return parts.join(' | ') || '完整';
};

const getShortStatusText = (collection) => {
  if (collection.status === 'unlinked') return '未关联TMDb';
  if (collection.status === 'tmdb_error') return 'TMDb错误';
  const missingCount = getMissingCount(collection);
  if (missingCount > 0) { return `缺失 ${missingCount} 部`; }
  const inLibraryCount = collection.in_library_count || 0;
  return `完整 ${inLibraryCount} 部`;
};

watch(() => props.taskStatus.is_running, (isRunning, wasRunning) => {
  // 我们关心的是任务从“正在运行”变为“已结束”的那个瞬间
  if (wasRunning && !isRunning) {
    // 同时，我们只关心与“合集”相关的任务结束事件
    if (props.taskStatus.current_action.includes('合集')) {
      message.info('后台合集任务已结束，正在刷新数据...');
      // 调用我们现有的数据加载函数，来获取最新的数据
      loadCachedData();
    }
  }
});

const isTooltipNeeded = (collection) => {
  return getFullStatusText(collection) !== getShortStatusText(collection);
};

const extractYear = (dateStr) => {
  if (!dateStr) return null;
  return dateStr.substring(0, 4);
};
</script>

<style scoped>
.collections-page { padding: 0 10px; }
.center-container { display: flex; justify-content: center; align-items: center; height: calc(100vh - 200px); }
.card-poster-container { flex-shrink: 0; width: 120px; height: 180px; }
.card-poster { width: 100%; height: 100%; }
.poster-placeholder { display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; background-color: var(--n-action-color); }
.card-content-container { flex-grow: 1; display: flex; flex-direction: column; padding: 12px 12px 12px 0; min-width: 0; }
.card-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; flex-shrink: 0; }
.card-title { font-weight: 600; font-size: 1.1em; line-height: 1.3; }
.card-status-area { flex-grow: 1; padding-top: 8px; }
.last-checked-text { display: block; font-size: 0.8em; margin-top: 6px; }
.card-actions { border-top: 1px solid var(--n-border-color); padding-top: 8px; margin-top: 8px; display: flex; justify-content: space-around; align-items: center; flex-shrink: 0; }
.modal-header { display: flex; justify-content: space-between; align-items: center; width: 100%; }
.movie-card { overflow: hidden; border-radius: 8px; }
.movie-poster { width: 100%; height: auto; aspect-ratio: 2 / 3; object-fit: cover; background-color: #eee; }
.movie-info { padding: 8px; text-align: center; height: 70px; display: flex; align-items: center; justify-content: center; }
.movie-title {
  font-weight: bold;
  max-width: 100%;
  word-break: break-word;
  white-space: normal;
  line-height: 1.3;
}
.loader-trigger {
  height: 50px;
  display: flex;
  justify-content: center;
  align-items: center;
}
</style>
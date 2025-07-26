<!-- src/components/CollectionsPage.vue (最终毕业版 - 弹窗终极修复) -->
<template>
  <n-layout content-style="padding: 24px;">
    <div class="collections-page">
      <n-page-header>
        <template #title>
          电影合集检查
        </template>
        <!-- ✨ [核心修改] 将统计摘要移动到 footer 插槽，信息更丰富 -->
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
            <n-tag v-if="globalStats.totalIgnored > 0" type="default" :bordered="false" round>
              {{ globalStats.totalIgnored }} 部已忽略
            </n-tag>
            <n-tag v-if="globalStats.totalMissingMovies === 0 && globalStats.totalCollections > 0" type="success" :bordered="false" round>
              所有合集均无缺失
            </n-tag>
          </n-space>
        </template>
        <template #extra>
          <n-space>
            <n-popconfirm @positive-click="ignoreAllMissingMovies" :disabled="globalStats.totalMissingMovies === 0">
              <template #trigger>
                <n-tooltip>
                  <template #trigger>
                    <n-button circle :loading="isIgnoringAll" :disabled="globalStats.totalMissingMovies === 0">
                      <template #icon><n-icon :component="IgnoreIcon" /></template>
                    </n-button>
                  </template>
                  一键忽略所有缺失
                </n-tooltip>
              </template>
              确定要忽略所有 {{ globalStats.totalMissingMovies }} 部缺失的电影吗？此操作会影响 {{ globalStats.collectionsWithMissing }} 个合集。
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
                    <!-- ✨ [核心修改] 使用 Tooltip 包裹 Tag -->
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
          <n-tab-pane name="missing" :tab="`缺失影片 (${missingMoviesInModal.length})`" :disabled="missingMoviesInModal.length === 0">
            <n-empty v-if="missingMoviesInModal.length === 0" description="所有已上映的缺失影片都已被订阅或忽略。" style="margin-top: 40px;"></n-empty>
            <n-grid v-else cols="2 s:3 m:4 l:5 xl:6" :x-gap="16" :y-gap="16" responsive="screen">
              <n-gi v-for="movie in missingMoviesInModal" :key="movie.tmdb_id">
                <n-card class="movie-card" content-style="padding: 0;">
                  <template #cover><img :src="getTmdbImageUrl(movie.poster_path)" class="movie-poster" /></template>
                  <div class="movie-info"><div class="movie-title">{{ movie.title }}<br />({{ extractYear(movie.release_date) || '未知年份' }})</div></div>
                  <template #action>
                    <n-button-group size="small" style="width: 100%;">
                      <n-button @click="subscribeToMovie(movie.tmdb_id, movie.title)" type="primary" :loading="subscribing[movie.tmdb_id]" style="width: 50%;">订阅</n-button>
                      <n-tooltip>
                        <template #trigger>
                          <n-button @click="ignoreMovie(selectedCollection.emby_collection_id, movie.tmdb_id)" style="width: 50%;"><template #icon><n-icon :component="IgnoreIcon" /></template>忽略</n-button>
                        </template>
                        不再自动订阅此电影
                      </n-tooltip>
                    </n-button-group>
                  </template>
                </n-card>
              </n-gi>
            </n-grid>
          </n-tab-pane>
          <!-- 未上映 -->
          <n-tab-pane name="unreleased" :tab="`未上映 (${unreleasedMoviesInModal.length})`" :disabled="unreleasedMoviesInModal.length === 0">
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
          <!-- 已忽略 -->
          <n-tab-pane name="ignored" :tab="`已忽略 (${ignoredMoviesInModal.length})`" :disabled="ignoredMoviesInModal.length === 0">
            <n-empty v-if="ignoredMoviesInModal.length === 0" description="您没有忽略此合集中的任何影片。" style="margin-top: 40px;"></n-empty>
            <n-grid v-else cols="2 s:3 m:4 l:5 xl:6" :x-gap="16" :y-gap="16" responsive="screen">
              <n-gi v-for="movie in ignoredMoviesInModal" :key="movie.tmdb_id">
                <n-card class="movie-card" content-style="padding: 0;">
                  <template #cover><img :src="getTmdbImageUrl(movie.poster_path)" class="movie-poster" /></template>
                  <div class="movie-info"><div class="movie-title">{{ movie.title }}<br />({{ extractYear(movie.release_date) || '未知年份' }})</div></div>
                  <template #action>
                    <n-button @click="unignoreMovie(selectedCollection.emby_collection_id, movie.tmdb_id)" type="info" size="small" block ghost>
                      <template #icon><n-icon :component="UnignoreIcon" /></template>
                      取消忽略
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
import { NLayout, NPageHeader, NDivider, NEmpty, NTag, NButton, NSpace, NIcon, useMessage, NTooltip, NGrid, NGi, NCard, NImage, NEllipsis, NSpin, NAlert, NModal, NTabs, NTabPane, NButtonGroup } from 'naive-ui';
import { SyncOutline, AlbumsOutline as AlbumsIcon, EyeOutline as EyeIcon, BanOutline as IgnoreIcon, CheckmarkCircleOutline as UnignoreIcon } from '@vicons/ionicons5';
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

// ✨ [核心修复] 回归到最简单的 ref，直接存储被点击的对象
const selectedCollection = ref(null);
const isIgnoringAll = ref(false);
const displayCount = ref(50);
const INCREMENT = 50;
const loaderRef = ref(null);
let observer = null;

const getMissingCount = (collection) => {
  if (!collection || !Array.isArray(collection.missing_movies)) return 0;
  return collection.missing_movies.filter(m => m.status === 'missing').length;
};

const missingCollections = computed(() => {
  return collections.value.filter(c => getMissingCount(c) > 0);
});

const globalStats = computed(() => {
  const stats = {
    totalCollections: collections.value.length,
    collectionsWithMissing: 0,
    totalMissingMovies: 0,
    totalUnreleased: 0,
    totalIgnored: 0,
  };

  for (const collection of collections.value) {
    if (Array.isArray(collection.missing_movies)) {
      const missingCount = collection.missing_movies.filter(m => m.status === 'missing').length;
      if (missingCount > 0) {
        stats.collectionsWithMissing++;
        stats.totalMissingMovies += missingCount;
      }
      stats.totalUnreleased += collection.missing_movies.filter(m => m.status === 'unreleased').length;
      stats.totalIgnored += collection.missing_movies.filter(m => m.status === 'ignored').length;
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

const missingMoviesInModal = computed(() => {
  if (!selectedCollection.value || !Array.isArray(selectedCollection.value.missing_movies)) return [];
  return selectedCollection.value.missing_movies.filter(movie => movie.status === 'missing');
});

const unreleasedMoviesInModal = computed(() => {
  if (!selectedCollection.value || !Array.isArray(selectedCollection.value.missing_movies)) return [];
  return selectedCollection.value.missing_movies.filter(movie => movie.status === 'unreleased');
});

const ignoredMoviesInModal = computed(() => {
  if (!selectedCollection.value || !Array.isArray(selectedCollection.value.missing_movies)) return [];
  return selectedCollection.value.missing_movies.filter(movie => movie.status === 'ignored');
});

const loadCachedData = async () => {
  if (collections.value.length === 0) isInitialLoading.value = true;
  error.value = null;
  try {
    // ✨ [核心修复] 添加 headers 配置，强制浏览器不缓存此 API 请求
    const response = await axios.get('/api/collections/status', {
      headers: {
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Expires': '0',
      },
    });
    collections.value = response.data;
    displayCount.value = 50;
  } catch (err) {
    error.value = err.response?.data?.error || '无法加载合集数据。';
  } finally {
    isInitialLoading.value = false;
  }
};

const ignoreAllMissingMovies = async () => {
  isIgnoringAll.value = true;
  try {
    const response = await axios.post('/api/collections/ignore_all_missing');
    message.success(response.data.message || '操作成功！');
    // 操作成功后，立即刷新数据以更新UI
    await loadCachedData();
  } catch (err) {
    message.error(err.response?.data?.error || '一键忽略操作失败。');
  } finally {
    isIgnoringAll.value = false;
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

watch(() => props.taskStatus.is_running, (isRunning, wasRunning) => {
  if (wasRunning && !isRunning && props.taskStatus.current_action.includes('合集')) {
    message.info('后台合集任务已结束，正在刷新数据...');
    loadCachedData();
  }
});

// ✨ [核心修复] 函数现在接收完整的 collection 对象
const openMissingMoviesModal = (collection) => {
  selectedCollection.value = collection;
  showModal.value = true;
};

const subscribeToMovie = async (tmdb_id, title) => {
  subscribing.value[tmdb_id] = true;
  try {
    await axios.post('/api/subscribe/moviepilot', { tmdb_id, title });
    message.success(`《${title}》已提交订阅`);
    if (selectedCollection.value) {
      const movieIndex = selectedCollection.value.missing_movies.findIndex(m => String(m.tmdb_id) === String(tmdb_id));
      if (movieIndex > -1) {
        selectedCollection.value.missing_movies.splice(movieIndex, 1);
      }
    }
  } catch (err) {
    message.error(err.response?.data?.error || '订阅失败');
  } finally {
    subscribing.value[tmdb_id] = false;
  }
};

const updateMovieStatus = async (collectionId, movieTmdbId, newStatus) => {
  try {
    await axios.post('/api/collections/ignore_movie', {
      collection_id: collectionId,
      movie_tmdb_id: movieTmdbId,
      new_status: newStatus
    });
    
    const movie = selectedCollection.value.missing_movies.find(m => String(m.tmdb_id) === String(movieTmdbId));
    if (movie) {
      movie.status = newStatus;
    }
    message.success(`操作成功！`);
  } catch (err) {
    message.error(err.response?.data?.error || '操作失败');
  }
};

const ignoreMovie = (collectionId, movieTmdbId) => {
  updateMovieStatus(collectionId, movieTmdbId, 'ignored');
};

const unignoreMovie = (collectionId, movieTmdbId) => {
  updateMovieStatus(collectionId, movieTmdbId, 'missing');
};

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

const formatTimestamp = (timestamp) => {
  if (!timestamp) return '从未';
  try { return format(new Date(timestamp * 1000), 'MM-dd HH:mm'); } 
  catch (e) { return 'N/A'; }
};
const getCollectionPosterUrl = (posterPath) => posterPath ? `/image_proxy${posterPath}` : '/img/poster-placeholder.png';
const getTmdbImageUrl = (posterPath) => posterPath ? `https://image.tmdb.org/t/p/w300${posterPath}` : '/img/poster-placeholder.png';

const getStatusTagType = (collection) => {
  if (collection.status === 'unlinked' || collection.status === 'tmdb_error') return 'error';
  const missingCount = getMissingCount(collection);
  if (missingCount > 0) return 'warning';
  return 'success';
};
const getFullStatusText = (collection) => {
  if (collection.status === 'unlinked') return '未关联TMDb';
  if (collection.status === 'tmdb_error') return 'TMDb错误';

  const missingCount = getMissingCount(collection);
  if (missingCount > 0) {
    return `缺失 ${missingCount} 部`;
  }

  const parts = [];
  const inLibraryCount = collection.in_library_count || 0;
  if (inLibraryCount > 0) {
    parts.push(`完整 ${inLibraryCount} 部`);
  }

  if (Array.isArray(collection.missing_movies)) {
      const unreleasedCount = collection.missing_movies.filter(m => m.status === 'unreleased').length;
      const ignoredCount = collection.missing_movies.filter(m => m.status === 'ignored').length;
      if (unreleasedCount > 0) {
        parts.push(`未上映 ${unreleasedCount} 部`);
      }
      if (ignoredCount > 0) {
        parts.push(`已忽略 ${ignoredCount} 部`);
      }
  }
  
  return parts.join(' | ') || '完整';
};

const getShortStatusText = (collection) => {
  if (collection.status === 'unlinked') return '未关联TMDb';
  if (collection.status === 'tmdb_error') return 'TMDb错误';

  const missingCount = getMissingCount(collection);
  if (missingCount > 0) {
    return `缺失 ${missingCount} 部`;
  }
  
  const inLibraryCount = collection.in_library_count || 0;
  return `完整 ${inLibraryCount} 部`;
};

const isTooltipNeeded = (collection) => {
  // 只有当完整文本和简洁文本不同时，才需要显示 Tooltip
  return getFullStatusText(collection) !== getShortStatusText(collection);
};
const getSmartStatusText = (collection) => {
  if (collection.status === 'unlinked') return '未关联TMDb';
  if (collection.status === 'tmdb_error') return 'TMDb错误';

  const missingCount = getMissingCount(collection);
  if (missingCount > 0) {
    return `缺失 ${missingCount} 部`;
  }

  // 当缺失为0时，显示详细信息
  const parts = [];
  const inLibraryCount = collection.in_library_count || 0;
  if (inLibraryCount > 0) {
    parts.push(`完整 ${inLibraryCount} 部`);
  }

  if (Array.isArray(collection.missing_movies)) {
      const unreleasedCount = collection.missing_movies.filter(m => m.status === 'unreleased').length;
      const ignoredCount = collection.missing_movies.filter(m => m.status === 'ignored').length;
      if (unreleasedCount > 0) {
        parts.push(`未上映 ${unreleasedCount} 部`);
      }
      if (ignoredCount > 0) {
        parts.push(`已忽略 ${ignoredCount} 部`);
      }
  }
  
  // 如果没有任何信息（例如一个空的合集），则显示“完整”
  return parts.join(' | ') || '完整';
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
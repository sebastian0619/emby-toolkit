<!-- src/components/DatabaseStats.vue -->
<template>
  <div>
    <n-page-header title="数据看板" subtitle="了解您媒体库的核心数据统计" style="margin-bottom: 24px;"></n-page-header>
    
    <div v-if="loading" style="text-align: center; padding: 40px;">
      <n-spin size="large" />
      <p style="margin-top: 10px;">正在加载统计数据...</p>
    </div>

    <div v-else-if="error" style="text-align: center; padding: 40px;">
      <n-alert title="加载失败" type="error">
        {{ error }}
      </n-alert>
    </div>

    <n-grid v-else :x-gap="24" :y-gap="24" :cols="4" responsive="screen" item-responsive>
      <!-- 核心媒体库统计 -->
      <n-gi span="4 m:2 l:1">
        <n-card title="核心媒体库" :bordered="false">
          <n-statistic label="已索引媒体总数" :value="stats.media_metadata?.total" />
          <n-divider />
          <n-space justify="space-around">
            <n-statistic label="电影" :value="stats.media_metadata?.movies" />
            <n-statistic label="剧集" :value="stats.media_metadata?.series" />
          </n-space>
        </n-card>
      </n-gi>

      <!-- 合集管理统计 -->
      <n-gi span="4 m:2 l:1">
        <n-card title="合集管理" :bordered="false">
          <n-statistic label="已识别TMDB合集" :value="stats.collections?.total_tmdb_collections" />
          <n-statistic label="存在缺失的合集" :value="stats.collections?.collections_with_missing" />
          <n-divider />
          <n-statistic label="活跃的自建合集" :value="stats.collections?.total_custom_collections" />
        </n-card>
      </n-gi>
      
      <!-- 订阅服务统计 -->
      <n-gi span="4 m:4 l:2">
        <n-card title="订阅服务" :bordered="false">
          <n-grid :x-gap="12" :y-gap="12" :cols="3">
            <n-gi>
              <n-statistic label="追剧中" :value="stats.subscriptions?.watchlist_active" />
            </n-gi>
            <n-gi>
              <n-statistic label="已暂停" :value="stats.subscriptions?.watchlist_paused" />
            </n-gi>
            <n-gi>
              <n-statistic label="已完结" :value="stats.subscriptions?.watchlist_ended" />
            </n-gi>
            <n-gi>
              <n-statistic label="演员订阅数" :value="stats.subscriptions?.actor_subscriptions_active" />
            </n-gi>
            <n-gi>
              <n-statistic label="追踪的媒体" :value="stats.subscriptions?.tracked_media_total" />
            </n-gi>
             <n-gi>
              <n-statistic label="已入库媒体" :value="stats.subscriptions?.tracked_media_in_library" />
            </n-gi>
          </n-grid>
        </n-card>
      </n-gi>

      <!-- 系统状态统计 -->
      <n-gi span="4 m:4 l:4">
        <n-card title="系统与缓存" :bordered="false">
          <n-grid :x-gap="12" :y-gap="12" :cols="4" responsive="screen" item-responsive>
            <n-gi span="4 s:2 m:1">
              <n-statistic label="数据库大小">
                {{ stats.system?.db_size_mb }} MB
              </n-statistic>
            </n-gi>
            <n-gi span="4 s:2 m:1">
              <n-statistic label="翻译缓存条目" :value="stats.system?.translation_cache_count" />
            </n-gi>
            <n-gi span="4 s:2 m:1">
              <n-statistic label="成功处理日志" :value="stats.system?.processed_log_count" />
            </n-gi>
            <n-gi span="4 s:2 m:1">
              <n-statistic label="失败处理日志" :value="stats.system?.failed_log_count" />
            </n-gi>
          </n-grid>
        </n-card>
      </n-gi>
    </n-grid>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import axios from 'axios';
import { 
  NPageHeader, NGrid, NGi, NCard, NStatistic, NDivider, NSpace, NSpin, NAlert 
} from 'naive-ui';

const loading = ref(true);
const error = ref(null);
const stats = ref({});

const fetchStats = async () => {
  loading.value = true;
  error.value = null;
  try {
    const response = await axios.get('/api/database/stats');
    if (response.data.status === 'success') {
      stats.value = response.data.data;
    } else {
      throw new Error(response.data.message || '获取统计数据失败');
    }
  } catch (e) {
    console.error('获取数据库统计失败:', e);
    error.value = e.message;
  } finally {
    loading.value = false;
  }
};

onMounted(() => {
  fetchStats();
});
</script>

<style scoped>
.n-statistic {
  text-align: center;
}
</style>
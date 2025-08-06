<!-- src/components/DatabaseStats.vue (并列布局优化版) -->
<template>
  <div>
    <n-page-header title="数据看板" subtitle="了解您媒体库的核心数据统计" style="margin-bottom: 24px;"></n-page-header>
    
    <div v-if="loading" class="loading-container">
      <n-spin size="large" />
      <p>正在加载统计数据...</p>
    </div>

    <div v-else-if="error" class="error-container">
      <n-alert title="加载失败" type="error">{{ error }}</n-alert>
    </div>

    <n-grid v-else :x-gap="24" :y-gap="24" :cols="4" responsive="screen" item-responsive>
      <!-- 卡片1: 核心媒体库 -->
      <n-gi span="4 m:2 l:1">
        <n-card title="核心媒体库" :bordered="false" class="content-card">
          <n-space vertical size="large" align="center">
            <n-statistic label="已索引媒体总数" class="centered-statistic">
              <span class="stat-value">{{ stats.media_metadata?.total }}</span>
            </n-statistic>
            <n-divider />
            <n-space justify="space-around" style="width: 100%;">
              <n-statistic label="电影" class="centered-statistic">
                <template #prefix>
                  <n-icon-wrapper :size="20" :border-radius="5" color="#3366FF44">
                    <n-icon :size="14" :component="FilmIcon" color="#3366FF" />
                  </n-icon-wrapper>
                </template>
                {{ stats.media_metadata?.movies }}
              </n-statistic>
              <n-statistic label="剧集" class="centered-statistic">
                <template #prefix>
                  <n-icon-wrapper :size="20" :border-radius="5" color="#33CC9944">
                    <n-icon :size="14" :component="TvIcon" color="#33CC99" />
                  </n-icon-wrapper>
                </template>
                {{ stats.media_metadata?.series }}
              </n-statistic>
            </n-space>
          </n-space>
        </n-card>
      </n-gi>

      <!-- 卡片2: 合集管理 -->
      <n-gi span="4 m:2 l:1">
        <n-card title="合集管理" :bordered="false" class="content-card">
          <n-space vertical size="large" align="center">
            <n-statistic label="已识别TMDB合集" class="centered-statistic" :value="stats.collections?.total_tmdb_collections" />
            <n-statistic label="存在缺失的合集" class="centered-statistic" :value="stats.collections?.collections_with_missing" />
            <n-divider />
            <n-statistic label="活跃的自建合集" class="centered-statistic">
              <span class="stat-value">{{ stats.collections?.total_custom_collections }}</span>
            </n-statistic>
          </n-space>
        </n-card>
      </n-gi>
      
      <!-- 卡片3: 订阅服务 -->
      <n-gi span="4 m:4 l:2">
        <n-card title="订阅服务" :bordered="false" class="content-card">
          <n-grid :x-gap="12" :y-gap="20" :cols="3">
            <n-gi><n-statistic label="追剧中" class="centered-statistic" :value="stats.subscriptions?.watchlist_active" /></n-gi>
            <n-gi><n-statistic label="已暂停" class="centered-statistic" :value="stats.subscriptions?.watchlist_paused" /></n-gi>
            <n-gi><n-statistic label="已完结" class="centered-statistic" :value="stats.subscriptions?.watchlist_ended" /></n-gi>
            <n-gi><n-statistic label="演员订阅" class="centered-statistic" :value="stats.subscriptions?.actor_subscriptions_active" /></n-gi>
            <n-gi><n-statistic label="追踪的媒体" class="centered-statistic" :value="stats.subscriptions?.tracked_media_total" /></n-gi>
            <n-gi><n-statistic label="已入库" class="centered-statistic" :value="stats.subscriptions?.tracked_media_in_library" /></n-gi>
          </n-grid>
        </n-card>
      </n-gi>

      <!-- ★ 修改点1: 调整 span 属性以实现并列布局 -->
      <n-gi span="4 l:2">
        <n-card title="系统与缓存" :bordered="false" class="content-card">
          <n-grid :x-gap="12" :y-gap="16" :cols="4" item-responsive>
            <n-gi span="2 s:1">
              <n-statistic label="数据库大小" class="centered-statistic">
                {{ stats.system?.db_size_mb }} MB
              </n-statistic>
            </n-gi>
            <n-gi span="2 s:1">
              <n-statistic label="翻译缓存条目" class="centered-statistic" :value="stats.system?.translation_cache_count" />
            </n-gi>
            <n-gi span="2 s:1">
              <n-statistic label="成功处理日志" class="centered-statistic" :value="stats.system?.processed_log_count" />
            </n-gi>
            <n-gi span="2 s:1">
              <n-statistic label="失败处理日志" class="centered-statistic" :value="stats.system?.failed_log_count" />
            </n-gi>
          </n-grid>
        </n-card>
      </n-gi>

      <!-- ★ 修改点2: 调整 span 属性并确保卡片高度一致 -->
      <n-gi span="4 l:2">
        <n-card 
          title="实时日志" 
          :bordered="false" 
          class="content-card"
          style="display: flex; flex-direction: column; height: 100%;" 
          content-style="flex-grow: 1; display: flex; flex-direction: column; padding: 0 24px 24px 24px;"
          header-style="padding-bottom: 12px;"
        >
          <template #header-extra>
            <n-button text @click="isLogViewerVisible = true" title="查看历史归档日志">
              <template #icon><n-icon :component="DocumentTextOutline" /></template>
              历史日志
            </n-button>
          </template>
          <n-log ref="logRef" :log="logContent" trim class="log-panel" style="flex-grow: 1;"/>
        </n-card>
      </n-gi>
    </n-grid>

    <!-- 历史日志查看器模态框 -->
    <LogViewer v-model:show="isLogViewerVisible" />
  </div>
</template>

<script setup>
import { ref, onMounted, computed, watch, nextTick } from 'vue';
import axios from 'axios';
import { 
  NPageHeader, NGrid, NGi, NCard, NStatistic, NSpin, NAlert, NIcon, NSpace, NDivider, NIconWrapper,
  NLog, NButton
} from 'naive-ui';
import { FilmOutline as FilmIcon, TvOutline as TvIcon, DocumentTextOutline } from '@vicons/ionicons5';
import LogViewer from './LogViewer.vue'; // 确保 LogViewer 组件路径正确

// --- Props ---
const props = defineProps({
  taskStatus: {
    type: Object,
    required: true,
    default: () => ({
      is_running: false,
      current_action: '空闲',
      logs: []
    })
  }
});

// --- 数据看板相关状态 ---
const loading = ref(true);
const error = ref(null);
const stats = ref({});

// --- 日志相关状态 ---
const logRef = ref(null);
const isLogViewerVisible = ref(false);

// --- Computed ---
const logContent = computed(() => props.taskStatus?.logs?.join('\n') || '等待任务日志...');

// --- Watchers ---
watch(() => props.taskStatus.logs, async () => {
  await nextTick();
  logRef.value?.scrollTo({ position: 'bottom', slient: true });
}, { deep: true });


// --- Methods ---
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
    error.value = e.message || '请求失败，请检查网络或联系管理员。';
  } finally {
    loading.value = false;
  }
};

// --- Lifecycle Hooks ---
onMounted(() => {
  fetchStats();
});
</script>

<style scoped>
.loading-container, .error-container {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  height: 400px;
}
.loading-container p {
  margin-top: 15px;
}
.centered-statistic {
  text-align: center;
}
.stat-value {
  font-size: 1.8em; /* 放大核心数字 */
  font-weight: 600;
  line-height: 1.2;
}
.centered-statistic .n-statistic__prefix {
  margin-right: 6px;
}

/* 确保所有卡片在栅格中高度一致 */
.content-card {
  height: 100%;
}

.log-panel {
  font-size: 13px;
  line-height: 1.6;
  background-color: transparent;
}
</style>
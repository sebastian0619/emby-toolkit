<!-- src/components/ResubscribePage.vue (生产力终极版) -->
<template>
  <n-layout content-style="padding: 24px;">
    <div class="resubscribe-page">
      <n-page-header>
        <template #title>
          <n-space align="center">
            <span>媒体洗版</span>
            <n-tag v-if="allItems.length > 0" type="info" round :bordered="false" size="small">
              {{ filteredItems.length }} / {{ allItems.length }} 项
            </n-tag>
          </n-space>
        </template>
        <n-alert title="操作提示" type="info" style="margin-top: 24px;">
          先进行洗版规则设定，然后点击刷新按钮扫描全库媒体项，扫描完刷新页面会展示所有媒体项并按照预设的洗版规则显示是否需要洗版。<br />
          按住Shift键可以进行多选然后批量操作。<br />
          本模块所有涉及删除的功能都是删除Emby的媒体项和媒体文件，危险操作，慎用！！！
        </n-alert>
        <template #extra>
          <n-space>
            <!-- ★★★ 批量操作下拉菜单 ★★★ -->
            <n-dropdown 
              trigger="click"
              :options="batchActions"
              @select="handleBatchAction"
            >
              <n-button>
                批量操作 ({{ selectedItems.size }})
              </n-button>
            </n-dropdown>

            <n-radio-group v-model:value="filter" size="small">
              <n-radio-button value="all">全部</n-radio-button>
              <n-radio-button value="needed">需洗版</n-radio-button>
              <n-radio-button value="ignored">已忽略</n-radio-button>
            </n-radio-group>
            <n-button @click="showSettingsModal = true">洗版规则设定</n-button>
            <n-button type="warning" @click="triggerResubscribeAll" :loading="isTaskRunning('全库媒体洗版')">一键洗版全部</n-button>
            <n-button type="primary" @click="triggerRefreshStatus" :loading="isTaskRunning('刷新媒体洗版状态')" circle>
              <template #icon><n-icon :component="SyncOutline" /></template>
            </n-button>
          </n-space>
        </template>
      </n-page-header>
      <n-divider />

      <div v-if="isLoading" class="center-container"><n-spin size="large" /></div>
      <div v-else-if="error" class="center-container"><n-alert title="加载错误" type="error">{{ error }}</n-alert></div>
      <div v-else-if="displayedItems.length > 0">
        <n-grid cols="1 s:1 m:2 l:3 xl:4" :x-gap="20" :y-gap="20" responsive="screen">
          <n-gi v-for="(item, index) in displayedItems" :key="item.item_id">
            <!-- ★★★ 卡片增加点击事件和选中样式绑定 ★★★ -->
            <n-card 
              class="dashboard-card series-card" 
              :bordered="false"
              :class="{ 'card-selected': selectedItems.has(item.item_id) }"
              @click="handleCardClick($event, item, index)"
            >
            <n-checkbox
                class="card-checkbox"
                :checked="selectedItems.has(item.item_id)"
              />

              <div class="card-poster-container" @click="handleCardClick($event, item, index)">
                <n-image lazy :src="getPosterUrl(item.item_id)" class="card-poster" object-fit="cover" />
              </div>

              <div class="card-content-container">
                <div class="card-header">
                  <n-ellipsis class="card-title">{{ item.item_name }}</n-ellipsis>
                </div>
                <div class="card-status-area">
                  <n-space vertical size="small">
                    <n-tag :type="getStatusInfo(item.status).type" size="small">
                      {{ getStatusInfo(item.status).text }}
                    </n-tag>
                    <div>
                      <n-tooltip v-if="item.status === 'needed'">
                        <template #trigger>
                          <n-text :depth="3" class="reason-text" :line-clamp="2">原因: {{ item.reason }}</n-text>
                        </template>
                        {{ item.reason }}
                      </n-tooltip>
                      <n-text v-else :depth="3" class="reason-text" :line-clamp="2" style="visibility: hidden;">占位</n-text>
                    </div>
                    <n-divider style="margin: 4px 0;" />
                    <n-text :depth="2" class="info-text">分辨率: {{ item.resolution_display }}</n-text>
                    <n-text :depth="2" class="info-text">质量: {{ item.quality_display }}</n-text>
                    <n-text :depth="2" class="info-text">特效: {{ item.effect_display }}</n-text>
                    <n-tooltip><template #trigger><n-text :depth="2" class="info-text" :line-clamp="1">音轨: {{ item.audio_display }}</n-text></template>{{ item.audio_display }}</n-tooltip>
                    <n-tooltip><template #trigger><n-text :depth="2" class="info-text" :line-clamp="1">字幕: {{ item.subtitle_display }}</n-text></template>{{ item.subtitle_display }}</n-tooltip>
                  </n-space>
                </div>
                <div class="card-actions">
                  <n-button v-if="item.status === 'needed'" size="small" type="primary" @click.stop="resubscribeItem(item)" :loading="subscribing[item.item_id]">洗版订阅</n-button>
                  <n-button v-else-if="item.status === 'subscribed'" size="small" type="info" disabled>已订阅</n-button>
                  <n-button v-else-if="item.status === 'ignored'" size="small" type="tertiary" @click.stop="unignoreItem(item)">取消忽略</n-button>
                  <n-button v-else size="small" style="visibility: hidden;">占位按钮</n-button>
                  <n-button text @click.stop="openInEmby(item.item_id)"><template #icon><n-icon :component="EmbyIcon" size="18" /></template></n-button>
                  <n-button text tag="a" :href="`https://www.themoviedb.org/${item.item_type === 'Movie' ? 'movie' : 'tv'}/${item.tmdb_id}`" target="_blank" @click.stop><template #icon><n-icon :component="TMDbIcon" size="18" /></template></n-button>
                </div>
              </div>
            </n-card>
          </n-gi>
        </n-grid>
        <div ref="loaderTrigger" class="loader-trigger">
          <n-spin v-if="displayedItems.length < filteredItems.length" />
        </div>
      </div>
      <div v-else class="center-container"><n-empty description="缓存为空，或当前筛选条件下无项目。" size="huge" /></div>
    </div>

    <n-modal v-model:show="showSettingsModal" preset="card" style="width: 90%; max-width: 800px;" title="洗版规则设定">
      <ResubscribeSettingsPage @saved="showSettingsModal = false" />
    </n-modal>
  </n-layout>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed, h, watch, nextTick } from 'vue';
import axios from 'axios';
import { NLayout, NPageHeader, NDivider, NEmpty, NTag, NButton, NSpace, NIcon, useMessage, NGrid, NGi, NCard, NImage, NEllipsis, NSpin, NAlert, NRadioGroup, NRadioButton, NModal, NTooltip, NText, NDropdown, useDialog } from 'naive-ui';
import { SyncOutline } from '@vicons/ionicons5';
import { useConfig } from '../composables/useConfig.js';
import ResubscribeSettingsPage from './settings/ResubscribeSettingsPage.vue';

const EmbyIcon = () => h('svg', { xmlns: "http://www.w.org/2000/svg", viewBox: "0 0 48 48", width: "18", height: "18" }, [ h('path', { d: "M24,4.2c-11,0-19.8,8.9-19.8,19.8S13,43.8,24,43.8s19.8-8.9,19.8-19.8S35,4.2,24,4.2z M24,39.8c-8.7,0-15.8-7.1-15.8-15.8S15.3,8.2,24,8.2s15.8,7.1,15.8,15.8S32.7,39.8,24,39.8z", fill: "currentColor" }), h('polygon', { points: "22.2,16.4 22.2,22.2 16.4,22.2 16.4,25.8 22.2,25.8 22.2,31.6 25.8,31.6 25.8,25.8 31.6,31.6 31.6,22.2 25.8,22.2 25.8,16.4 ", fill: "currentColor" })]);
const TMDbIcon = () => h('svg', { xmlns: "http://www.w.org/2000/svg", viewBox: "0 0 512 512", width: "18", height: "18" }, [ h('path', { d: "M256 512A256 256 0 1 0 256 0a256 256 0 1 0 0 512zM133.2 176.6a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zm63.3-22.4a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm74.8 108.2c-27.5-3.3-50.2-26-53.5-53.5a8 8 0 0 1 16-.6c2.3 19.3 18.8 34 38.1 31.7a8 8 0 0 1 7.4 8c-2.3.3-4.5.4-6.8.4zm-74.8-108.2a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm149.7 22.4a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zM133.2 262.6a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8zm63.3-22.4a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm74.8 108.2c-27.5-3.3-50.2-26-53.5-53.5a8 8 0 0 1 16-.6c2.3 19.3 18.8 34 38.1 31.7a8 8 0 0 1 7.4 8c-2.3.3-4.5.4-6.8.4zm-74.8-108.2a22.4 22.4 0 1 1 44.8 0 22.4 22.4 0 1 1 -44.8 0zm149.7 22.4a22.4 22.4 0 1 1 0-44.8 22.4 22.4 0 1 1 0 44.8z", fill: "#01b4e4" })]);

const { configModel } = useConfig();
const message = useMessage();
const dialog = useDialog();
const props = defineProps({ taskStatus: { type: Object, required: true } });

const allItems = ref([]); 
const displayedItems = ref([]); 
const filter = ref('all');
const isLoading = ref(true);
const error = ref(null);
const showSettingsModal = ref(false);
const subscribing = ref({});
const loaderTrigger = ref(null); 
const PAGE_SIZE = 24; 

// ★★★ 多选功能 Refs ★★★
const selectedItems = ref(new Set());
const lastSelectedIndex = ref(-1);

const isTaskRunning = (taskName) => props.taskStatus.is_running && props.taskStatus.current_action.includes(taskName);

const filteredItems = computed(() => {
  if (filter.value === 'needed') return allItems.value.filter(item => item.status === 'needed');
  if (filter.value === 'ignored') return allItems.value.filter(item => item.status === 'ignored');
  return allItems.value;
});

const getStatusInfo = (status) => {
  switch (status) {
    case 'needed': return { text: '需洗版', type: 'warning' };
    case 'subscribed': return { text: '已订阅', type: 'info' };
    case 'ignored': return { text: '已忽略', type: 'tertiary' };
    case 'ok': default: return { text: '质量达标', type: 'success' };
  }
};

const fetchData = async () => {
  isLoading.value = true;
  error.value = null;
  selectedItems.value.clear(); // 刷新时清空选择
  lastSelectedIndex.value = -1;
  try {
    const response = await axios.get('/api/resubscribe/library_status');
    allItems.value = response.data;
  } catch (err) {
    error.value = err.response?.data?.error || '获取洗版状态失败。';
  } finally {
    isLoading.value = false;
  }
};

const loadMore = () => {
  if (isLoading.value || displayedItems.value.length >= filteredItems.value.length) return;
  const currentLength = displayedItems.value.length;
  const nextItems = filteredItems.value.slice(currentLength, currentLength + PAGE_SIZE);
  displayedItems.value.push(...nextItems);
};

let observer = null;
const setupObserver = () => {
  if (observer) observer.disconnect();
  nextTick(() => {
    if (loaderTrigger.value) {
      observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting) loadMore();
      }, { rootMargin: '200px' });
      observer.observe(loaderTrigger.value);
    }
  });
};

watch(filteredItems, (newFilteredItems) => {
  displayedItems.value = newFilteredItems.slice(0, PAGE_SIZE);
  setupObserver();
}, { immediate: true });

onMounted(fetchData);
onUnmounted(() => { if (observer) observer.disconnect(); });

const handleCardClick = (event, item, index) => {
  const itemId = item.item_id;
  const isSelected = selectedItems.value.has(itemId);

  if (event.shiftKey && lastSelectedIndex.value !== -1) {
    const start = Math.min(lastSelectedIndex.value, index);
    const end = Math.max(lastSelectedIndex.value, index);
    for (let i = start; i <= end; i++) {
      const idInRange = displayedItems.value[i].item_id;
      // Shift 选区的逻辑是：全部变成和当前点击项“相反”的状态
      // 但为了简单和直观，我们统一为“全部选中”
      selectedItems.value.add(idInRange);
    }
  } else {
    // 普通点击，就是切换状态
    if (isSelected) {
      selectedItems.value.delete(itemId);
    } else {
      selectedItems.value.add(itemId);
    }
  }
  lastSelectedIndex.value = index;
};

// ★★★ 批量操作逻辑 ★★★
const batchActions = computed(() => {
  const actions = [];
  const noSelection = selectedItems.value.size === 0;

  // 1. 基础批量操作 (基于勾选)
  if (filter.value === 'ignored') {
    actions.push({ 
      label: '批量取消忽略', 
      key: 'unignore', 
      disabled: noSelection
    });
  } else {
    actions.push({ 
      label: '批量订阅', 
      key: 'subscribe', 
      disabled: noSelection
    });
    actions.push({ 
      label: '批量忽略', 
      key: 'ignore', 
      disabled: noSelection
    });
  }
  actions.push({ 
    label: '批量删除', 
    key: 'delete', 
    props: { type: 'error' }, 
    disabled: noSelection
  });
  
  // 2. 分割线
  actions.push({ type: 'divider', key: 'd1' });

  // 3. “一键”操作 (基于当前视图，且不冗余)
  // ★★★ 核心修复：只保留“一键忽略”和“一键删除” ★★★
  
  if (filter.value === 'needed') {
    // 在“需洗版”视图，提供“一键忽略”
    actions.push({ label: '一键忽略当前页所有“需洗版”项', key: 'oneclick-ignore' });
  }
  if (filter.value === 'ignored') {
    // 在“已忽略”视图，提供“一键取消忽略”
    actions.push({ label: '一键取消忽略当前页所有项', key: 'oneclick-unignore' });
  }
  
  // 在“需洗版”和“已忽略”视图下，都提供“一键删除”
  if (filter.value === 'needed' || filter.value === 'ignored') {
      actions.push({ 
          label: `一键删除当前页所有“${filter.value === 'needed' ? '需洗版' : '已忽略'}”项`, 
          key: 'oneclick-delete',
          props: { type: 'error' } 
      });
  }
  
  return actions;
});

const handleBatchAction = (key) => {
  let ids = Array.from(selectedItems.value);
  let actionKey = key;
  let isOneClick = false;

  if (key.startsWith('oneclick-')) {
    isOneClick = true;
    actionKey = key.split('-')[1];
    ids = []; 
  }
  
  if (!isOneClick && ids.length === 0) return;
  executeBatchAction(actionKey, ids, isOneClick);
};

const sendBatchActionRequest = async (actionKey, ids, isOneClick) => {
  const actionMap = {
    subscribe: 'subscribe', ignore: 'ignore',
    unignore: 'ok', delete: 'delete'
  };
  const action = actionMap[actionKey];

  try {
    const response = await axios.post('/api/resubscribe/batch_action', {
      item_ids: ids,
      action: action,
      is_one_click: isOneClick,
      filter: filter.value
    });
    message.success(response.data.message);
    
    if (!isOneClick) {
      const optimisticStatusMap = { subscribe: 'subscribed', ignore: 'ignored', unignore: 'ok' };
      const optimisticStatus = optimisticStatusMap[actionKey];
      if (optimisticStatus === 'ok' || actionKey === 'delete') {
        allItems.value = allItems.value.filter(i => !ids.includes(i.item_id));
      } else {
        ids.forEach(id => {
          const item = allItems.value.find(i => i.item_id === id);
          if (item) item.status = optimisticStatus;
        });
      }
      selectedItems.value.clear();
    } else {
      fetchData();
    }
  } catch (err) {
    message.error(err.response?.data?.error || `批量操作失败。`);
  }
};

const executeBatchAction = async (actionKey, ids, isOneClick) => {
  // 对于危险操作，显示确认框
  if (actionKey === 'delete' || actionKey === 'oneclick-delete') {
    const countText = isOneClick ? `当前视图下所有` : `${ids.length}`;
    dialog.warning({
      title: '高危操作确认',
      content: `确定要永久删除选中的 ${countText} 个媒体项吗？此操作会从 Emby 和硬盘中删除文件，且不可恢复！`,
      positiveText: '我确定，删除！',
      negativeText: '取消',
      // ★★★ 核心修复：确认后，调用我们新建的“发货”函数 ★★★
      onPositiveClick: () => {
        sendBatchActionRequest(actionKey, ids, isOneClick);
      }
    });
  } else {
    // 对于安全操作，直接“发货”
    sendBatchActionRequest(actionKey, ids, isOneClick);
  }
};

// ★★★ 取消忽略逻辑 ★★★
const unignoreItem = async (item) => {
  try {
    await axios.post('/api/resubscribe/batch_action', {
      item_ids: [item.item_id],
      action: 'ok' // 后端需要支持 'ok' 动作
    });
    message.success(`《${item.item_name}》已取消忽略。`);
    item.status = 'ok'; // 乐观更新
  } catch (err) {
    message.error(err.response?.data?.error || '取消忽略失败。');
  }
};

const triggerRefreshStatus = async () => { try { await axios.post('/api/resubscribe/refresh_status'); message.success('刷新任务已提交，请稍后查看任务状态。'); } catch (err) { message.error(err.response?.data?.error || '提交刷新任务失败。'); }};
const triggerResubscribeAll = async () => { try { await axios.post('/api/resubscribe/resubscribe_all'); message.success('一键洗版任务已提交，请稍后查看任务状态。'); } catch (err) { message.error(err.response?.data?.error || '提交一键洗版任务失败。'); }};
const resubscribeItem = async (item) => { subscribing.value[item.item_id] = true; try { const response = await axios.post('/api/resubscribe/resubscribe_item', { item_id: item.item_id, item_name: item.item_name, tmdb_id: item.tmdb_id, item_type: item.item_type, }); message.success(response.data.message); const itemInList = allItems.value.find(i => i.item_id === item.item_id); if (itemInList) { itemInList.status = 'subscribed'; } } catch (err) { message.error(err.response?.data?.error || '洗版订阅失败。'); } finally { subscribing.value[item.item_id] = false; }};
const getPosterUrl = (itemId) => `/image_proxy/Items/${itemId}/Images/Primary?maxHeight=480&tag=1`;
const openInEmby = (itemId) => { const embyServerUrl = configModel.value?.emby_server_url; const serverId = configModel.value?.emby_server_id; if (!embyServerUrl || !itemId) { message.error('Emby服务器地址未配置，无法跳转。'); return; } const baseUrl = embyServerUrl.endsWith('/') ? embyServerUrl.slice(0, -1) : embyServerUrl; let finalUrl = `${baseUrl}/web/index.html#!/item?id=${itemId}`; if (serverId) { finalUrl += `&serverId=${serverId}`; } window.open(finalUrl, '_blank'); };
watch(() => props.taskStatus.is_running, (isRunning, wasRunning) => { if (wasRunning && !isRunning) { const relevantActions = [ '刷新媒体洗版状态', '全库媒体洗版', ]; if (relevantActions.some(action => props.taskStatus.current_action.includes(action))) { message.info('相关后台任务已结束，正在刷新海报墙...'); fetchData(); } } });
</script>

<style scoped>
.card-checkbox {
  position: absolute;
  top: 8px;
  left: 8px;
  z-index: 10;
  background-color: rgba(24, 24, 28, 0.75);
  border-radius: 50%;
  padding: 4px;
  --n-color-checked: var(--n-color-primary-hover);
  --n-border-radius: 50%;
  
  /* 默认隐藏 */
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.2s ease-in-out, visibility 0.2s ease-in-out;
}

/* 鼠标悬浮在卡片上时，或者卡片已被选中时，都显示复选框 */
.series-card:hover .card-checkbox,
.card-selected .card-checkbox {
  opacity: 1;
  visibility: visible;
}
.card-selected {
  outline: 2px solid var(--n-color-primary-hover);
  outline-offset: 2px;
}
.series-card {
  cursor: pointer;
  transition: transform 0.2s ease-in-out;
}
.series-card:hover {
  transform: translateY(-4px);
}
.center-container { display: flex; justify-content: center; align-items: center; height: calc(100vh - 200px); }
.card-poster-container { flex-shrink: 0; width: 160px; height: 240px; overflow: hidden; }
.card-poster { width: 100%; height: 100%; }
.card-content-container { flex-grow: 1; display: flex; flex-direction: column; padding: 12px 8px 12px 0; min-width: 0; }
.card-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; flex-shrink: 0; }
.card-title { font-weight: 600; font-size: 1.1em; line-height: 1.3; }
.card-status-area { flex-grow: 1; padding-top: 8px; }
.reason-text { font-size: 0.85em; }
.info-text { font-size: 0.85em; }
.card-actions { border-top: 1px solid var(--n-border-color); padding-top: 8px; margin-top: 8px; display: flex; justify-content: space-around; align-items: center; flex-shrink: 0; }
.loader-trigger { height: 50px; display: flex; justify-content: center; align-items: center; }
.series-card.dashboard-card > :deep(.n-card__content) { flex-direction: row !important; justify-content: flex-start !important; padding: 12px !important; gap: 12px !important; }
</style>
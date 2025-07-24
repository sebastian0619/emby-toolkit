<template>
 <n-layout content-style="padding: 24px;">
  <div>
    <n-page-header>
      <template #title>
        演员订阅
      </template>
      <template #extra>
        <n-button type="primary" @click="handleAddSubscription">
          <template #icon>
            <n-icon><add-icon /></n-icon>
          </template>
          添加演员订阅
        </n-button>
      </template>
      <template #footer>
        <n-text depth="3">在这里管理您订阅的演员。系统将自动追踪他们的最新作品并提交订阅。</n-text>
      </template>
    </n-page-header>

    <div v-if="loading" style="text-align: center; margin-top: 50px;">
      <n-spin size="large" />
    </div>

    <div v-else-if="subscriptions.length === 0" style="margin-top: 20px;">
      <n-empty description="您还没有订阅任何演员">
        <template #extra>
          <n-button size="small" @click="handleAddSubscription">
            去添加一个
          </n-button>
        </template>
      </n-empty>
    </div>

    <n-grid :x-gap="16" :y-gap="16" cols="2 s:3 m:4 l:5 xl:6 xxl:7" responsive="screen" style="margin-top: 20px;">
      <n-gi v-for="sub in subscriptions" :key="sub.id">
        <n-card class="glass-section" @click="handleCardClick(sub)">
          <template #cover>
            <img :src="getImageUrl(sub.profile_path)" class="actor-avatar">
          </template>
          <div class="actor-name">{{ sub.actor_name }}</div>
          <div class="actor-status">
            <n-tag :type="sub.status === 'active' ? 'success' : 'warning'" size="small" round>
              {{ sub.status === 'active' ? '订阅中' : '已暂停' }}
            </n-tag>
          </div>
        </n-card>
      </n-gi>
    </n-grid>

    <add-subscription-modal
      :show="showAddModal"
      @update:show="showAddModal = $event"
      @subscription-added="onSubscriptionChange"
    />

    <subscription-details-modal
      :show="showDetailsModal"
      :subscription-id="selectedSubId"
      @update:show="showDetailsModal = $event"
      @subscription-updated="onSubscriptionChange" 
      @subscription-deleted="onSubscriptionChange"
    />
  </div>
  </n-layout>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { NPageHeader, NButton, NIcon, NText, NGrid, NGi, NCard, NSpin, NEmpty, NTag, useMessage } from 'naive-ui';
import { Add as AddIcon } from '@vicons/ionicons5';
import axios from 'axios';
import AddSubscriptionModal from './modals/AddSubscriptionModal.vue';
import SubscriptionDetailsModal from './modals/SubscriptionDetailsModal.vue';

const loading = ref(true);
const subscriptions = ref([]);
const showAddModal = ref(false);
const message = useMessage();
const showDetailsModal = ref(false); 
const selectedSubId = ref(null); 

const fetchSubscriptions = async () => {
  loading.value = true;
  try {
    const response = await axios.get('/api/actor-subscriptions');
    subscriptions.value = response.data;
  } catch (error) {
    console.error("获取演员订阅列表失败:", error);
    message.error('获取订阅列表失败，请稍后重试。');
  } finally {
    loading.value = false;
  }
};

const getImageUrl = (path) => {
  if (!path) {
    return 'https://via.placeholder.com/185x278.png?text=No+Image';
  }
  return `https://image.tmdb.org/t/p/w185${path}`;
};

const handleAddSubscription = () => {
  showAddModal.value = true;
};


const handleCardClick = (subscription) => {
  selectedSubId.value = subscription.id;
  showDetailsModal.value = true;
};

const onSubscriptionChange = () => {
  message.info('订阅列表已更新！');
  fetchSubscriptions(); 
};

onMounted(() => {
  fetchSubscriptions();
});

onMounted(() => {
  fetchSubscriptions();
});
</script>

<style scoped>
.actor-card {
  cursor: pointer;
  text-align: center;
  overflow: hidden;
}
.actor-avatar {
  width: 100%;
  height: auto;
  aspect-ratio: 2 / 3;
  object-fit: cover;
  background-color: #f0f2f5;
}
.dark-mode .actor-avatar {
  background-color: #2c2c32;
}
.actor-name {
  font-weight: 600;
  margin-top: 8px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.actor-status {
  margin-top: 4px;
  font-size: 12px;
}
</style>
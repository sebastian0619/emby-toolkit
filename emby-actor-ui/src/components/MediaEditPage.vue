<!-- src/components/MediaEditPage.vue -->
<template>
  <div class="media-edit-page" style="padding: 24px;">
    <n-page-header @back="goBack">
      <template #title>
        手动编辑媒体演员信息
      </template>
    </n-page-header>

    <n-divider />

    <div v-if="isLoading">
      <n-spin size="large" />
      <p style="text-align: center; margin-top: 10px;">正在加载媒体详情...</p>
    </div>
    <div v-else-if="itemDetails">
      <n-card :title="itemDetails.item_name || '加载中...'">
        <n-descriptions label-placement="left" bordered :column="1">
          <n-descriptions-item label="Emby ItemID">
            {{ itemDetails.item_id }} (不可编辑)
          </n-descriptions-item>
          <n-descriptions-item label="媒体类型">
            {{ itemDetails.item_type }}
          </n-descriptions-item>
          <n-descriptions-item label="原始记录评分" v-if="itemDetails.original_score !== null && itemDetails.original_score !== undefined">
            {{ itemDetails.original_score }}
          </n-descriptions-item>
          <n-descriptions-item label="待复核原因" v-if="itemDetails.review_reason">
            {{ itemDetails.review_reason }}
          </n-descriptions-item>
        </n-descriptions>

        <n-divider title-placement="left" style="margin-top: 20px;">演员列表</n-divider>
          <n-form label-placement="left" label-width="auto" style="margin-top: 10px;">
            <!-- ... -->
            
            <!-- ✨ 1. 修改外层 n-grid 的 cols 属性，实现多列响应式布局 ✨ -->
            <n-grid :cols="'1 640:2 1024:3 1280:4'" :x-gap="16" :y-gap="16">
              <n-grid-item v-for="(actor, index) in editableCast" :key="actor._temp_id">
                <n-card size="small">
                  <template #header>
                    <!-- 卡片头部可以保持不变，或者简化 -->
                    <span style="font-weight: normal;">
                      演员 {{ index + 1 }}
                      <n-text depth="3" style="font-size: 0.8em; margin-left: 5px;">(ID: {{ actor.embyPersonId }})</n-text>
                    </span>
                  </template>
                  <template #header-extra>
                    <!-- 上移、下移、删除按钮保持不变 -->
                    <n-space>
                      <!-- ... -->
                    </n-space>
                  </template>
                  
                  <!-- ✨ 2. 修改内层 n-grid，只保留演员名和角色名 ✨ -->
                  <n-grid x-gap="12" :cols="2">
                    <n-form-item-gi :span="1" label="演员名" label-placement="top">
                      <n-input v-model:value="actor.name" placeholder="演员名" />
                    </n-form-item-gi>
                    <n-form-item-gi :span="1" label="角色名" label-placement="top">
                      <n-input v-model:value="actor.role" placeholder="角色名" />
                    </n-form-item-gi>
                  </n-grid>
                  
                </n-card>
              </n-grid-item>
            </n-grid>
            
          </n-form>
        
        <div style="margin-top:20px; margin-bottom: 20px;"> 
         <n-button
          type="default"
          @click="refreshCastFromDouban"
          :loading="isRefreshingFromDouban"
          style="margin-right: 10px;"
          :disabled="isLoading || !itemDetails || !itemDetails.item_id" 
        >
          从豆瓣刷新演员表
         </n-button>
        <n-text depth="3" style="font-size: 0.85em;">(此操作将尝试用豆瓣信息更新下方列表中的现有演员)</n-text>
        </div>

        <template #action>
          <n-space justify="end">
            <n-button @click="goBack">返回列表</n-button>
            <n-button type="primary" @click="handleSaveChanges" :loading="isSaving">
              保存修改
            </n-button>
          </n-space>
        </template>
      </n-card>
    </div>
    <div v-else>
      <n-alert title="错误" type="error">
        无法加载媒体详情，或指定的媒体项不存在。
        <n-button text @click="goBack" style="margin-left: 10px;">返回列表</n-button>
      </n-alert>
    </div>
  </div>
</template>

<script setup>
import { NIcon } from 'naive-ui';
import { ref, computed, watch, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import axios from 'axios';
import { NTag, NPageHeader, NDivider, NSpin, NCard, NDescriptions, NDescriptionsItem, NButton, NSpace, NAlert, useMessage } from 'naive-ui';
import {
  ArrowUpOutline as ArrowUpIcon,
  ArrowDownOutline as ArrowDownIcon,
  TrashOutline as TrashIcon
} from '@vicons/ionicons5';

const route = useRoute();
const router = useRouter();
const message = useMessage();
const editableCast = ref([]);
const itemId = ref(null);
const isLoading = ref(true);
const itemDetails = ref(null); // 存储从后端获取的媒体完整信息
const isSaving = ref(false);

watch(() => itemDetails.value, (newItemDetails) => {
  if (newItemDetails && newItemDetails.current_emby_cast) {
    editableCast.value = JSON.parse(JSON.stringify(newItemDetails.current_emby_cast)).map((actor, index) => ({
      _temp_id: `actor-${Date.now()}-${index}`,
      embyPersonId: actor.embyPersonId,
      name: actor.name || '',
      role: actor.role || '',
      imdbId: actor.imdbId || '',
      doubanId: actor.doubanId || '',
      tmdbId: actor.tmdbId || '',
      matchStatus: actor.matchStatus || '原始' // 初始状态为“原始”或从API获取
    }));
    console.log("Editable cast initialized:", editableCast.value);
  } else {
    editableCast.value = [];
  }
}, { deep: true }); // deep watch 以防万一，但通常 newItemDetails 替换时就会触发

const removeActor = (index) => {
  editableCast.value.splice(index, 1);
  message.info("已从编辑列表移除一个演员（尚未保存）。");
};

const moveActorUp = (index) => {
  if (index > 0 && editableCast.value && editableCast.value.length > index) {
    const actorToMove = editableCast.value.splice(index, 1)[0]; // 从原位置移除
    editableCast.value.splice(index - 1, 0, actorToMove); // 插入到新位置
    message.info("演员顺序已调整（上移）。");
  }
};

const moveActorDown = (index) => {
  if (editableCast.value && editableCast.value.length > index + 1) {
    const actorToMove = editableCast.value.splice(index, 1)[0]; // 从原位置移除
    editableCast.value.splice(index + 1, 0, actorToMove); // 插入到新位置
    message.info("演员顺序已调整（下移）。");
  }
};

const isRefreshingFromDouban = ref(false);

const refreshCastFromDouban = async () => {
  if (!itemDetails.value || !itemDetails.value.item_id) {
    message.error("没有正在编辑的媒体项的ID。");
    return;
  }
  isRefreshingFromDouban.value = true;
  try {
    const itemId = itemDetails.value.item_id;
    
    // ✨ 调用新的API端点
    const response = await axios.post(`/api/preview_processed_cast/${itemId}`);
    const processedActorsFromApi = response.data;

    if (processedActorsFromApi && Array.isArray(processedActorsFromApi)) {
      if (processedActorsFromApi.length === 0) {
        message.info("处理器返回了一个空的演员列表。");
        // 你可以选择清空UI上的列表
        // editableCast.value = []; 
        return;
      } 
      
      // ✨ 用返回的完整列表直接替换UI上的列表
      // 这是最简单直接的方式，确保UI和处理结果完全一致
      editableCast.value = processedActorsFromApi.map((actor, index) => ({
        _temp_id: `actor-${Date.now()}-${index}`, // 为v-for生成临时key
        embyPersonId: actor.embyPersonId || '',
        name: actor.name || '',
        role: actor.role || '',
        imdbId: actor.imdbId || '',
        doubanId: actor.doubanId || '',
        tmdbId: actor.tmdbId || '',
        matchStatus: actor.matchStatus || '已刷新'
      }));

      message.success(`演员列表已根据核心处理器预览结果刷新 (${processedActorsFromApi.length}位)。请检查并保存。`);
      
    } else {
      message.error("刷新演员信息失败或返回格式不正确。");
    }
  } catch (error) {
    console.error("刷新演员列表失败:", error);
    message.error(error.response?.data?.error || "刷新演员列表失败，请查看控制台日志。");
  } finally {
    isRefreshingFromDouban.value = false;
  }
};

const getMatchStatusType = (status) => {
  if (status && status.includes('已匹配')) return 'success';
  if (status && status.includes('待确认')) return 'warning';
  if (status === 'refreshed_from_douban') return 'info'; // 可以自定义
  return 'default';
};

const fetchMediaDetails = async () => {
  if (!itemId.value) {
    message.error("无效的媒体项ID！");
    isLoading.value = false;
    return;
  }
  isLoading.value = true;
  try {
    const response = await axios.get(`/api/media_with_cast_for_editing/${itemId.value}`);
    if (response.data) {
      itemDetails.value = response.data;
      console.log("获取到的媒体详情:", itemDetails.value);
    } else {
      message.error("未能获取媒体详情数据。");
      itemDetails.value = null; // 清空以显示错误提示
    }
  } catch (error) {
    console.error("获取媒体详情失败:", error);
    message.error(error.response?.data?.error || "获取媒体详情失败，请查看控制台日志。");
    itemDetails.value = null; // 清空以显示错误提示
  } finally {
    isLoading.value = false;
  }
};

onMounted(() => {
  itemId.value = route.params.itemId; // 从路由参数中获取 itemId
  if (itemId.value) {
    fetchMediaDetails();
  } else {
    message.error("未提供媒体项ID！");
    isLoading.value = false;
    // 可以选择导航回列表页
    // router.replace({ name: 'ReviewList' });
  }
});

const goBack = () => {
  router.push({ name: 'ReviewList' }); // 或者 router.go(-1) 返回上一页
};

const handleSaveChanges = async () => {
  if (!itemDetails.value || !itemDetails.value.item_id) {
    message.error("没有有效的媒体项ID来保存更改。");
    return;
  }
  if (!editableCast.value || editableCast.value.length === 0) {
    // 如果用户删光了所有演员，需要确认是否真的要清空演员列表
    // 这里我们先假设至少要有一个演员，或者由后端 emby_handler 处理空列表的情况
    // message.warning("演员列表为空，确定要这样保存吗？（此功能待完善确认）");
    // return;
    // 或者，如果允许清空演员，直接继续
    console.warn("[MediaEditPage] 准备保存一个空的演员列表。");
  }

isSaving.value = true;
  try {
    const itemIdToSave = itemDetails.value.item_id;
    
    // 准备发送给后端的数据
    const castPayload = editableCast.value.map(actor => ({
      embyPersonId: actor.embyPersonId, // 必须
      name: actor.name,                 // 必须
      role: actor.role || '',           // 可选，空字符串代替null
      imdbId: actor.imdbId || null,     // 发送null如果为空
      doubanId: actor.doubanId || null,
      tmdbId: actor.tmdbId || null
    }));

    const payload = {
      cast: castPayload,
      // （可选）传递 item_name 和 item_type 给后端用于日志或刷新
      item_name: itemDetails.value.item_name,
      item_type: itemDetails.value.item_type
    };

    console.log(`[MediaEditPage] 发送给 /api/update_media_cast/${itemIdToSave} 的数据:`, payload);
    await axios.post(`/api/update_media_cast/${itemIdToSave}`, payload);

    message.success("演员信息已成功更新到Emby！");
    
    // 保存成功后，导航回列表页
    // 在导航前，可以考虑给用户一点时间看成功消息，或者直接跳转
    // setTimeout(() => { // 可选的延迟
    //   router.push({ name: 'ReviewList' });
    // }, 1500);
    
    // 为了能立即看到列表刷新，最好是 ReviewList 页面在 onActivated 或 onMounted 时重新获取数据
    // 或者这里 $emit 一个事件给父组件（如果 ReviewList 是父组件或通过布局管理）
    // emit('castUpdated'); // 如果 ReviewList 监听这个事件来刷新
    
    // 最简单直接的方式：
    router.push({ name: 'ReviewList' }); // 假设 ReviewList 在激活时会重新加载数据

  } catch (error) {
    console.error("保存修改失败:", error);
    message.error(error.response?.data?.error || "保存修改失败，请查看后端日志。");
  } finally {
    isSaving.value = false;
  }
};
</script>

<style scoped>
.media-edit-page {
  max-width: 1280px;
  margin: 0 auto;
}
</style>
<style>
/* 这是一个全局样式块，专门解决演员列表标签的显示问题 */

/* 针对4列布局 (1280px以上) */
@media (min-width: 1280px) {
  .n-grid-item:nth-child(n + 5) .n-form-item .n-form-item-label {
    display: none !important;
  }
}

/* 针对3列布局 (1024px - 1279px) */
@media (min-width: 1024px) and (max-width: 1279.98px) {
  .n-grid-item:nth-child(n + 4) .n-form-item .n-form-item-label {
    display: none !important;
  }
}

/* 针对2列布局 (640px - 1023px) */
@media (min-width: 640px) and (max-width: 1023.98px) {
  .n-grid-item:nth-child(n + 3) .n-form-item .n-form-item-label {
    display: none !important;
  }
}

/* 针对1列布局 (640px以下) */
@media (max-width: 639.98px) {
  .n-grid-item:nth-child(n + 2) .n-form-item .n-form-item-label {
    display: none !important;
  }
}
</style>
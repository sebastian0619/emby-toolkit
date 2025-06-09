<template>
  <div class="media-edit-page">
    <n-page-header @back="goBack">
      <template #title>
        手动编辑媒体演员信息
      </template>
    </n-page-header>

    <n-divider />

    <div v-if="isLoading" class="loading-container">
      <n-spin size="large" />
      <p style="text-align: center; margin-top: 10px;">正在加载媒体详情...</p>
    </div>

    <div v-else-if="itemDetails && itemDetails.item_name">
      <!-- 
        ★★★ 调整参数 [1]: 页面主布局 - 海报与演员列表的宽度比例 ★★★
        - `cols`: 定义了总列数。`1` 表示默认1列，`l:3` 表示在 large 屏幕及以上是3列。
        - `span`: 定义了子项占据的列数。
        - 当前设置 (l:3, l:1, l:2) 表示在大屏幕上，左侧海报占 1/3，右侧演员列表占 2/3。
        - 示例: 想让海报更宽，可以改为 `cols="1 l:5"`，左侧 `span="1 l:2"`，右侧 `span="1 l:3"`。
      -->
      <n-grid cols="1 l:4" :x-gap="24" responsive="screen">
        <!-- 左侧信息栏 (海报) -->
        <n-grid-item span="1 l:1">
          <n-space vertical :size="24">
            <n-card :title="itemDetails.item_name" :bordered="false">
              <template #cover>
                <n-image
                  :src="posterUrl"
                  lazy
                  object-fit="cover"
                  class="media-poster"
                >
                  <template #placeholder>
                    <div class="poster-placeholder">
                      <n-icon :component="ImageIcon" size="48" :depth="3" />
                    </div>
                  </template>
                </n-image>
              </template>
              <template #header-extra>
                <n-tag :type="itemDetails.item_type === 'Movie' ? 'info' : 'success'" size="small" round>
                  {{ itemTypeInChinese }}
                </n-tag>
              </template>
              <n-descriptions label-placement="left" bordered :column="1" size="small">
                <n-descriptions-item label="Emby ItemID">
                  {{ itemDetails.item_id }}
                </n-descriptions-item>
                <n-descriptions-item label="原始记录评分" v-if="itemDetails.original_score !== null && itemDetails.original_score !== undefined">
                  <n-tag type="warning" size="small">{{ itemDetails.original_score }}</n-tag>
                </n-descriptions-item>
                <n-descriptions-item label="待复核原因" v-if="itemDetails.review_reason">
                  <n-text type="error">{{ itemDetails.review_reason }}</n-text>
                </n-descriptions-item>
              </n-descriptions>
            </n-card>

            <n-card title="辅助工具" :bordered="false">
              <!-- ... 辅助工具部分 ... -->
              <n-space vertical>
                <n-form-item label="数据操作" label-placement="top">
                  <n-space>
                    <n-button 
                      tag="a" 
                      :href="searchLinks.google_search_wiki"
                      target="_blank" 
                      :disabled="!searchLinks.google_search_wiki"
                      :loading="isLoading"
                    >
                      Google搜索
                    </n-button>
                    <n-button
                      type="default"
                      @click="refreshCastFromDouban"
                      :loading="isRefreshingFromDouban"
                      :disabled="isLoading" 
                    >
                      从豆瓣刷新
                    </n-button>
                    <n-button
                      type="info"
                      @click="translateAllFields" 
                      :loading="isTranslating" 
                      :disabled="isLoading"
                    >
                      一键翻译
                    </n-button>
                  </n-space>
                </n-form-item>
                <n-form-item label="从URL提取" label-placement="top">
                  <n-input-group>
                    <n-input 
                      v-model:value="urlToParse" 
                      placeholder="粘贴维基百科演员表URL"
                      clearable
                    />
                    <n-button 
                      type="primary" 
                      @click="parseCastFromUrl" 
                      :loading="isParsingFromUrl"
                      :disabled="!urlToParse"
                    >
                      提取
                    </n-button>
                  </n-input-group>
                  <template #feedback>
                    <n-text depth="3" style="font-size: 0.85em;">
                      提取后将自动与下方列表进行匹配更新。
                    </n-text>
                  </template>
                </n-form-item>
              </n-space>
            </n-card>
          </n-space>
        </n-grid-item>

        <!-- 右侧演员列表 -->
        <n-grid-item span="1 l:3">
          <n-card title="演员列表" :bordered="false">
            
            <n-form label-placement="left" label-width="auto">
              <!-- 
                ★★★ 调整参数 [2]: 演员列表的响应式列数 ★★★
                - `cols`: 定义了在不同屏幕尺寸下显示的列数。
                - `s:2`: small 屏幕及以上显示2列。
                - `l:3`: large 屏幕及以上显示3列。
                - `xl:4`: extra large 屏幕及以上显示4列。
                - 您可以根据需要自由增删或修改这些值，例如改为 `l:2 xl:3 2xl:4`。
              -->
              <n-grid cols="1 s:2 m:2 l:3 xl:4 2xl:5" :x-gap="16" :y-gap="16" responsive="screen">
                <n-grid-item v-for="(actor, index) in editableCast" :key="actor._temp_id">
                  <n-card size="small" class="actor-edit-card" content-style="padding: 12px;">
                    <template #header>
                      <div class="actor-card-header">
                        <n-avatar
                          round
                          size="small"
                          :style="{ backgroundColor: getAvatarColor(actor.name) }"
                        >
                          {{ index + 1 }}
                        </n-avatar>
                        <span class="actor-name-title" :title="actor.name">{{ actor.name || '新演员' }}</span>
                      </div>
                    </template>
                    <template #header-extra>
                      <n-space>
                        <n-button text @click="moveActorUp(index)" :disabled="index === 0">
                          <n-icon :component="ArrowUpIcon" />
                        </n-button>
                        <n-button text @click="moveActorDown(index)" :disabled="index === editableCast.length - 1">
                          <n-icon :component="ArrowDownIcon" />
                        </n-button>
                        <n-popconfirm @positive-click="removeActor(index)">
                          <template #trigger>
                            <n-button text type="error">
                              <n-icon :component="TrashIcon" />
                            </n-button>
                          </template>
                          确定要删除演员 “{{ actor.name || '新演员' }}” 吗？
                        </n-popconfirm>
                      </n-space>
                    </template>
                    
                    <div class="actor-card-content">
                      <n-image
                        :src="getActorImageUrl(actor)"
                        lazy
                        object-fit="cover"
                        class="actor-avatar-image"
                      >
                        <template #placeholder>
                          <div class="avatar-placeholder">
                            <n-icon :component="PersonIcon" size="24" :depth="3" />
                          </div>
                        </template>
                      </n-image>
                      
                      <div class="actor-inputs">
                        <n-form-item label="演员" label-placement="left" label-width="40" class="compact-form-item">
                          <n-input v-model:value="actor.name" placeholder="演员名" size="small" />
                        </n-form-item>
                        <n-form-item label="角色" label-placement="left" label-width="40" class="compact-form-item">
                          <n-input v-model:value="actor.role" placeholder="角色名" size="small" />
                        </n-form-item>
                      </div>
                    </div>
                  </n-card>
                </n-grid-item>
              </n-grid>
            </n-form>
            
            <div class="sticky-actions">
              <n-space>
                <n-button @click="goBack">返回列表</n-button>
                <n-button type="primary" @click="handleSaveChanges" :loading="isSaving">
                  保存修改
                </n-button>
              </n-space>
            </div>
          </n-card>
        </n-grid-item>
      </n-grid>
    </div>

    <div v-else class="error-container">
      <n-alert title="错误" type="error">
        无法加载媒体详情，或指定的媒体项不存在。请检查后端日志或确认该媒体项有效。
        <n-button text @click="goBack" style="margin-left: 10px;">返回列表</n-button>
      </n-alert>
    </div>
  </div>
</template>

<script setup>
// ... 你的 import 保持不变，但需要新增 NFormItem
import { NIcon, NInput, NInputGroup, NGrid, NGridItem, NFormItem, NTag, NAvatar, NPopconfirm, NImage } from 'naive-ui';
import { ref, onMounted, watch, computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import axios from 'axios';
import { NPageHeader, NDivider, NSpin, NCard, NDescriptions, NDescriptionsItem, NButton, NSpace, NAlert, useMessage } from 'naive-ui';
import {
  ArrowUpOutline as ArrowUpIcon,
  ArrowDownOutline as ArrowDownIcon,
  TrashOutline as TrashIcon,
  ImageOutline as ImageIcon,
  PersonOutline as PersonIcon
} from '@vicons/ionicons5';

// ... 你的 setup 函数中的所有逻辑保持完全不变 ...
// 我们不再需要 AddIcon，所以可以从 import 中移除
const route = useRoute();
const router = useRouter();
const message = useMessage();

const itemId = ref(null);
const isLoading = ref(true);
const itemDetails = ref(null);
const editableCast = ref([]);
const isSaving = ref(false);

const searchLinks = ref({ google_search_wiki: '' });
const isParsingFromUrl = ref(false);
const urlToParse = ref('');
const isRefreshingFromDouban = ref(false);
const isTranslating = ref(false);

const posterUrl = computed(() => {
  if (itemDetails.value?.item_id && itemDetails.value?.image_tag) {
    return `/image_proxy/Items/${itemDetails.value.item_id}/Images/Primary?tag=${itemDetails.value.image_tag}&quality=90`;
  }
  return '';
});

const getActorImageUrl = (actor) => {
  if (actor.embyPersonId && actor.image_tag) {
    return `/image_proxy/Items/${actor.embyPersonId}/Images/Primary?tag=${actor.image_tag}&quality=90`;
  }
  return '';
};

// ★★★ START: 1. 新增计算属性，用于转换媒体类型为中文 ★★★
const itemTypeInChinese = computed(() => {
  if (!itemDetails.value || !itemDetails.value.item_type) {
    return ''; // 如果数据还没加载好，返回空
  }
  
  // 根据后端返回的 item_type 值进行判断
  switch (itemDetails.value.item_type) {
    case 'Movie':
      return '电影';
    case 'Series':
      return '电视剧';
    // 你可以根据需要添加更多类型，比如 'Episode' -> '单集'
    default:
      return itemDetails.value.item_type; // 如果是未知类型，直接显示原文
  }
});

const getAvatarColor = (name) => {
  const colors = ['#f56a00', '#7265e6', '#ffbf00', '#00a2ae', '#4caf50', '#2196f3'];
  if (!name || name.length === 0) return colors[0];
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  const index = Math.abs(hash % colors.length);
  return colors[index];
};

watch(() => itemDetails.value, (newItemDetails) => {
  if (newItemDetails && newItemDetails.current_emby_cast) {
    editableCast.value = JSON.parse(JSON.stringify(newItemDetails.current_emby_cast)).map((actor, index) => ({
      _temp_id: `actor-${Date.now()}-${index}`,
      embyPersonId: actor.embyPersonId,
      name: actor.name || '',
      role: actor.role || '',
      image_tag: actor.image_tag || '',
      matchStatus: actor.matchStatus || '原始'
    }));
  } else {
    editableCast.value = [];
  }
}, { deep: true });

const removeActor = (index) => {
  editableCast.value.splice(index, 1);
  message.info("已从编辑列表移除一个演员（尚未保存）。");
};

const moveActorUp = (index) => {
  if (index > 0) {
    const actorToMove = editableCast.value.splice(index, 1)[0];
    editableCast.value.splice(index - 1, 0, actorToMove);
  }
};

const moveActorDown = (index) => {
  if (index < editableCast.value.length - 1) {
    const actorToMove = editableCast.value.splice(index, 1)[0];
    editableCast.value.splice(index + 1, 0, actorToMove);
  }
};

const refreshCastFromDouban = async () => {
  if (!itemDetails.value?.item_id) return;
  isRefreshingFromDouban.value = true;
  try {
    const response = await axios.post(`/api/preview_processed_cast/${itemDetails.value.item_id}`);
    const processedActorsFromApi = response.data;

    if (processedActorsFromApi && Array.isArray(processedActorsFromApi)) {
      if (processedActorsFromApi.length === 0) {
        message.info("处理器返回了一个空的演员列表。");
        return;
      }
      
      editableCast.value = processedActorsFromApi.map((actor, index) => ({
        _temp_id: `actor-${Date.now()}-${index}`,
        embyPersonId: actor.embyPersonId || '',
        name: actor.name || '',
        role: actor.role || '',
        image_tag: actor.image_tag || '',
        matchStatus: actor.matchStatus || '已刷新'
      }));
      message.success(`演员列表已根据核心处理器预览结果刷新 (${processedActorsFromApi.length}位)。`);
    } else {
      message.error("刷新演员信息失败或返回格式不正确。");
    }
  } catch (error) {
    console.error("刷新演员列表失败:", error);
    message.error(error.response?.data?.error || "刷新演员列表失败。");
  } finally {
    isRefreshingFromDouban.value = false;
  }
};

const parseCastFromUrl = async () => {
  if (!urlToParse.value.trim()) {
    message.warning("请输入要解析的URL。");
    return;
  }
  isParsingFromUrl.value = true;
  try {
    const response = await axios.post('/api/parse_cast_from_url', { url: urlToParse.value });
    const newCastFromWeb = response.data;

    if (newCastFromWeb && Array.isArray(newCastFromWeb) && newCastFromWeb.length > 0) {
      message.success(`成功提取 ${newCastFromWeb.length} 位演员信息，正在与当前列表进行匹配更新...`);
      handleEnrich(newCastFromWeb);
    } else {
      message.info(response.data.message || "未从该URL中找到有效的演员信息。");
    }
  } catch (error) {
    console.error("从URL解析演员失败:", error);
    message.error(error.response?.data?.error || "解析失败，请检查URL或后端日志。");
  } finally {
    isParsingFromUrl.value = false;
  }
};

const handleEnrich = async (newCastFromWeb) => {
  try {
    const payload = {
      current_cast: editableCast.value,
      new_cast_from_web: newCastFromWeb
    };
    const response = await axios.post('/api/actions/enrich_cast_list', payload);
    const enrichedList = response.data;

    editableCast.value = enrichedList.map((actor, index) => ({
      ...actor,
      _temp_id: `enriched-actor-${Date.now()}-${index}`
    }));
    
    message.success("列表已更新！请检查匹配结果并保存。");
    urlToParse.value = '';

  } catch (error) {
    console.error("丰富列表失败:", error);
    message.error(error.response?.data?.error || "更新列表时发生错误，请检查后端日志。");
  }
};

const translateAllFields = async () => {
  if (!editableCast.value || editableCast.value.length === 0) {
    message.warning("演员列表为空，无需翻译。");
    return;
  }
  isTranslating.value = true;
  message.info("正在请求后端翻译所有非中文的姓名和角色名...");
  try {
    const payload = {
        cast: editableCast.value,
        item_id: itemDetails.value.item_id 
    };
    const response = await axios.post('/api/actions/translate_cast', { cast: editableCast.value });
    const translatedList = response.data;

    editableCast.value = translatedList.map((actor, index) => ({
      ...actor,
      _temp_id: `translated-actor-${Date.now()}-${index}`
    }));
    
    message.success("翻译完成！");

  } catch (error) {
    console.error("一键翻译失败:", error);
    message.error(error.response?.data?.error || "翻译失败，请检查后端日志。");
  } finally {
    isTranslating.value = false;
  }
};

const fetchMediaDetails = async () => {
  if (!itemId.value) {
    isLoading.value = false;
    return;
  }
  isLoading.value = true;
  try {
    const response = await axios.get(`/api/media_with_cast_for_editing/${itemId.value}`);
    itemDetails.value = response.data;
    
    if (response.data && response.data.search_links) {
      searchLinks.value = response.data.search_links;
    }
  } catch (error) {
    console.error("获取媒体详情失败:", error);
    message.error(error.response?.data?.error || "获取媒体详情失败。");
    itemDetails.value = null;
  } finally {
    isLoading.value = false;
  }
};

onMounted(() => {
  itemId.value = route.params.itemId;
  if (itemId.value) {
    fetchMediaDetails();
  } else {
    message.error("未提供媒体项ID！");
    isLoading.value = false;
  }
});

const goBack = () => {
  router.push({ name: 'ReviewList' });
};

const handleSaveChanges = async () => {
  if (!itemDetails.value?.item_id) return;
  isSaving.value = true;
  try {
    const castPayload = editableCast.value.map(actor => ({
      embyPersonId: actor.embyPersonId,
      name: actor.name,
      role: actor.role || '',
    }));

    const payload = {
      cast: castPayload,
      item_name: itemDetails.value.item_name,
      item_type: itemDetails.value.item_type
    };

    await axios.post(`/api/update_media_cast/${itemDetails.value.item_id}`, payload);
    message.success("演员信息已成功更新到Emby！");
    router.push({ name: 'ReviewList' });
  } catch (error) {
    console.error("保存修改失败:", error);
    message.error(error.response?.data?.error || "保存修改失败。");
  } finally {
    isSaving.value = false;
  }
};
</script>

<style scoped>
.media-edit-page {
  padding: 0 24px 24px 24px;
  transition: all 0.3s;
}

.loading-container, .error-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 200px;
}

.media-poster {
  width: 100%;
  height: auto;
  background-color: var(--n-card-color);

  /* 
    ★★★ 调整参数 [3]: 海报的形状 (宽高比) ★★★
    - `2 / 3`: 标准电影海报比例。
    - `3 / 4`: 稍方正一些的比例。
    - `1 / 1`: 正方形。
  */
  aspect-ratio: 2 / 3;
}

.poster-placeholder, .avatar-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--n-action-color);
}

.actor-edit-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--n-box-shadow-hover) !important;
}

.actor-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.actor-name-title {
  font-weight: 600;
  flex-grow: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.actor-card-content {
  display: flex;
  align-items: center;
  gap: 12px;
}

.actor-avatar-image {
  /* 
    ★★★ 调整参数 [4]: 演员头像的大小 ★★★
    - 直接修改下面的 width 和 height 值即可。
    - 建议保持两者相等以维持正方形。
  */
  width: 100px;
  height: 100px;
  
  border-radius: var(--n-border-radius);
  flex-shrink: 0;
}

.actor-inputs {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex-grow: 1;
  flex-basis: 0;
  min-width: 0;
}

.compact-form-item {
  margin-bottom: 0 !important;
}

.sticky-actions {
  position: sticky;
  bottom: -24px;
  left: 0;
  right: 0;
  padding: 16px 24px;
  background-color: var(--n-color);
  border-top: 1px solid var(--n-border-color);
  display: flex;
  justify-content: flex-end;
  z-index: 10;
  margin: 24px -24px 0;
}
</style>
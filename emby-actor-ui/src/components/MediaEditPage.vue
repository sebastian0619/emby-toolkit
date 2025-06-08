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

        <n-divider title-placement="left" style="margin-top: 20px;">辅助工具</n-divider>
        <n-space vertical>
          <!-- 外部搜索按钮 -->
          <n-form-item label="外部搜索" label-placement="left">
            <n-space>
              <n-button 
                tag="a" 
                :href="searchLinks.google_url" 
                target="_blank" 
                :disabled="!searchLinks.google_url"
                :loading="isFetchingSearchLinks"
              >
                Google 搜索
              </n-button>
              <!-- ✨✨✨ 百度搜索按钮已移除 ✨✨✨ -->
            </n-space>
          </n-form-item>
          
          <!-- 从URL提取 -->
          <n-form-item label="从URL提取" label-placement="left">
            <n-input-group>
              <n-input 
                v-model:value="urlToParse" 
                placeholder="在此粘贴包含演员表的网页URL (如 IMDb, 维基百科)"
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
          </n-form-item>
        </n-space>

        <n-divider title-placement="left" style="margin-top: 20px;">演员列表</n-divider>
        <n-form label-placement="left" label-width="auto" style="margin-top: 10px;">
          <n-grid :cols="'1 640:2 1024:3 1280:4'" :x-gap="16" :y-gap="16">
            <n-grid-item v-for="(actor, index) in editableCast" :key="actor._temp_id">
              <n-card size="small">
                <template #header>
                  <span style="font-weight: normal;">
                    演员 {{ index + 1 }}
                    <n-text depth="3" style="font-size: 0.8em; margin-left: 5px;" v-if="actor.embyPersonId">(ID: {{ actor.embyPersonId }})</n-text>
                    <n-text depth="3" style="font-size: 0.8em; margin-left: 5px;" v-else>(新)</n-text>
                  </span>
                </template>
                <template #header-extra>
                  <n-space>
                    <n-button text @click="moveActorUp(index)" :disabled="index === 0">
                      <n-icon :component="ArrowUpIcon" />
                    </n-button>
                    <n-button text @click="moveActorDown(index)" :disabled="index === editableCast.length - 1">
                      <n-icon :component="ArrowDownIcon" />
                    </n-button>
                    <n-button text type="error" @click="removeActor(index)">
                      <n-icon :component="TrashIcon" />
                    </n-button>
                  </n-space>
                </template>
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
import { NIcon, NInput, NInputGroup, NGrid, NGridItem, NFormItem, NFormItemGi } from 'naive-ui';
import { ref, onMounted, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import axios from 'axios';
import { NTag, NPageHeader, NDivider, NSpin, NCard, NDescriptions, NDescriptionsItem, NButton, NSpace, NAlert, useMessage, useDialog } from 'naive-ui';
import {
  ArrowUpOutline as ArrowUpIcon,
  ArrowDownOutline as ArrowDownIcon,
  TrashOutline as TrashIcon
} from '@vicons/ionicons5';

const route = useRoute();
const router = useRouter();
const message = useMessage();
const dialog = useDialog();

const itemId = ref(null);
const isLoading = ref(true);
const itemDetails = ref(null);
const editableCast = ref([]);
const isSaving = ref(false);

const isFetchingSearchLinks = ref(false);
const searchLinks = ref({ google_url: '' }); // 只保留 google_url
const isParsingFromUrl = ref(false);
const urlToParse = ref('');

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
    message.info("演员顺序已调整（上移）。");
  }
};

const moveActorDown = (index) => {
  if (index < editableCast.value.length - 1) {
    const actorToMove = editableCast.value.splice(index, 1)[0];
    editableCast.value.splice(index + 1, 0, actorToMove);
    message.info("演员顺序已调整（下移）。");
  }
};

const isRefreshingFromDouban = ref(false);

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
        imdbId: actor.imdbId || '',
        doubanId: actor.doubanId || '',
        tmdbId: actor.tmdbId || '',
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

const fetchSearchLinks = async () => {
  if (!itemId.value) return;
  isFetchingSearchLinks.value = true;
  try {
    const response = await axios.get(`/api/generate_search_links/${itemId.value}`);
    searchLinks.value = response.data;
  } catch (error) {
    console.error("获取搜索链接失败:", error);
    message.error("获取外部搜索链接失败。");
  } finally {
    isFetchingSearchLinks.value = false;
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
      // 直接调用“仅丰富”逻辑
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
    // 调用新的、安全的后端API
    const response = await axios.post('/api/actions/enrich_cast_list', payload);
    const enrichedList = response.data;

    // 用后端返回的、已丰富的列表更新UI
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

const fetchMediaDetails = async () => {
  if (!itemId.value) {
    isLoading.value = false;
    return;
  }
  isLoading.value = true;
  try {
    const response = await axios.get(`/api/media_with_cast_for_editing/${itemId.value}`);
    itemDetails.value = response.data;
    fetchSearchLinks();
  } catch (error)
  {
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
      imdbId: actor.imdbId || null,
      doubanId: actor.doubanId || null,
      tmdbId: actor.tmdbId || null
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
  max-width: 1280px;
  margin: 0 auto;
}
</style>
<style>
/* 全局样式保持不变 */
@media (min-width: 1280px) {
  .n-grid-item:nth-child(n + 5) .n-form-item .n-form-item-label {
    display: none !important;
  }
}
@media (min-width: 1024px) and (max-width: 1279.98px) {
  .n-grid-item:nth-child(n + 4) .n-form-item .n-form-item-label {
    display: none !important;
  }
}
@media (min-width: 640px) and (max-width: 1023.98px) {
  .n-grid-item:nth-child(n + 3) .n-form-item .n-form-item-label {
    display: none !important;
  }
}
@media (max-width: 639.98px) {
  .n-grid-item:nth-child(n + 2) .n-form-item .n-form-item-label {
    display: none !important;
  }
}
</style>
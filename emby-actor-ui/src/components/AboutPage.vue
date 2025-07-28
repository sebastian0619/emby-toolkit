<!-- src/components/AboutPage.vue -->
<template>
  <n-layout content-style="padding: 24px;">
    <n-page-header title="关于软件">
      <template #extra>
        <n-button tag="a" :href="`https://github.com/${githubRepo}`" target="_blank" secondary>
          <template #icon><n-icon :component="LogoGithub" /></template>
          在 GitHub 上查看
        </n-button>
      </template>
    </n-page-header>
    <n-divider />

    <div v-if="isLoading" class="center-container"><n-spin size="large" /></div>
    <div v-else-if="error" class="center-container"><n-alert title="加载错误" type="error">{{ error }}</n-alert></div>
    
    <div v-else>
      <n-list hoverable clickable>
        <n-list-item v-for="(release, index) in releases" :key="release.version">
          <n-thing>
            <template #header>
              <n-space align="center">
                <a :href="release.url" target="_blank" class="version-link">{{ release.version }}</a>
                <n-tag v-if="index === 0" type="success" size="small" round>最新软件版本</n-tag>
                <n-tag v-if="release.version === currentVersion" type="info" size="small" round>当前版本</n-tag>
              </n-space>
            </template>
            <template #header-extra>
              <n-text :depth="3">{{ formatReleaseDate(release.published_at) }}</n-text>
            </template>
            <div class="changelog-content" v-html="renderMarkdown(release.changelog)"></div>
          </n-thing>
        </n-list-item>
      </n-list>
    </div>
  </n-layout>
</template>

<script setup>
import { ref, onMounted, computed, h } from 'vue';
import axios from 'axios';
import { marked } from 'marked';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import { NLayout, NPageHeader, NDivider, NSpin, NAlert, NList, NListItem, NThing, NTag, NSpace, NButton, NIcon, NText } from 'naive-ui';
import { LogoGithub } from '@vicons/ionicons5';

const githubRepoOwner = 'hbq0405';
const githubRepoName = 'emby-actor-processor';
const githubRepo = computed(() => `${githubRepoOwner}/${githubRepoName}`);
const isLoading = ref(true);
const error = ref(null);
const currentVersion = ref('');
const releases = ref([]);

const fetchData = async () => {
  isLoading.value = true;
  error.value = null;
  try {
    const response = await axios.get('/api/system/about_info');
    currentVersion.value = response.data.current_version;
    releases.value = response.data.releases;
  } catch (err) {
    error.value = err.response?.data?.error || '无法获取版本信息。';
  } finally {
    isLoading.value = false;
  }
};

const renderMarkdown = (markdownText) => {
  if (!markdownText) return '';
  // 配置 marked，将 `- ` 转换为无序列表
  return marked.parse(markdownText, { gfm: true, breaks: true });
};

const formatReleaseDate = (dateString) => {
  if (!dateString) return '';
  return formatDistanceToNow(parseISO(dateString), { addSuffix: true, locale: zhCN });
};

onMounted(fetchData);
</script>

<style scoped>
.center-container {
  display: flex;
  justify-content: center;
  align-items: center;
  height: calc(100vh - 200px);
}
.version-link {
  font-size: 1.2em;
  font-weight: 600;
  color: var(--n-text-color);
  text-decoration: none;
}
.version-link:hover {
  text-decoration: underline;
}
.changelog-content {
  margin-top: 8px;
  padding-left: 4px;
  color: var(--n-text-color-2);
}
/* 深度选择器，用于修改 v-html 渲染出的内容的样式 */
.changelog-content :deep(ul) {
  padding-left: 20px;
  margin: 0;
}
.changelog-content :deep(li) {
  margin-bottom: 4px;
}
</style>
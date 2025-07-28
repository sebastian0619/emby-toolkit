<!-- src/components/AboutPage.vue -->
<template>
  <n-layout content-style="padding: 24px;">
    <n-page-header title="关于软件">
      <template #extra>
        <!-- ✨✨✨ 新增的打赏按钮 ✨✨✨ -->
          <n-tooltip>
            <template #trigger>
              <n-button @click="showSponsorModal = true" type="primary" ghost>
                <template #icon><n-icon :component="CafeIcon" /></template>
                请我喝杯奶茶
              </n-button>
            </template>
            用爱发电不易，您的支持是项目前进的最大动力！
          </n-tooltip>
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
        <n-list-item v-for="(release, index) in appStore.releases" :key="release.version">
          <n-thing>
            <template #header>
              <n-space align="center">
                <a :href="release.url" target="_blank" class="version-link">{{ release.version }}</a>
                <!-- vvv 核心修改：使用 appStore 的数据 vvv -->
                <n-tag v-if="release.version === appStore.latestVersion" type="success" size="small" round>最新软件版本</n-tag>
                <n-tag v-if="release.version === appStore.currentVersion" type="info" size="small" round>当前版本</n-tag>
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
    <!-- ✨✨✨ 新增的打赏弹窗 ✨✨✨ -->
    <n-modal v-model:show="showSponsorModal" preset="card" style="width: 90%; max-width: 400px;" title="支持开发者" :bordered="false">
      <div class="sponsor-content">
        <n-p>
          用ai发电也不易，喝杯奶茶行不行！
        </n-p>
        <n-p>
          您的支持，哪怕是一点点，都是我持续更新的最大动力。感谢您的慷慨！
        </n-p>
        <n-divider />
        
        <!-- +++ 核心修改：不再使用 grid，直接放一个居中的项目 +++ -->
        <div class="qr-code-item">
          <n-image width="200" src="/img/wechat_pay.png" />
          <n-text strong style="margin-top: 10px;">推荐使用微信支付</n-text>
        </div>

      </div>
    </n-modal>
  </n-layout>
</template>

<script setup>
import { ref, onMounted, computed, h } from 'vue'; // 确保导入 ref 和 computed
import axios from 'axios';
import { marked } from 'marked';
import { formatDistanceToNow, parseISO } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import { NLayout, NPageHeader, NDivider, NSpin, NAlert, NList, NListItem, NThing, NTag, NSpace, NButton, NIcon, NText } from 'naive-ui';
import { LogoGithub } from '@vicons/ionicons5';
import { useAppStore } from '../stores/app'; // 导入 appStore

const appStore = useAppStore(); // 使用 appStore


const githubRepoOwner = 'hbq0405';
const githubRepoName = 'emby-actor-processor';
const githubRepo = computed(() => `${githubRepoOwner}/${githubRepoName}`);
const isLoading = ref(true);
const error = ref(null);
const showSponsorModal = ref(false);
const currentVersion = ref('');
const releases = ref([]);
const fetchData = async () => {
  console.log("[AboutPage] 1. fetchData 开始执行...");
  isLoading.value = true;
  error.value = null;
  
  try {
    console.log("[AboutPage] 2. 准备调用 appStore.fetchVersionInfo()...");
    
    // 调用 store 的 action 来获取并填充数据
    await appStore.fetchVersionInfo();
    
    console.log("[AboutPage] 3. appStore.fetchVersionInfo() 调用成功完成！");
    console.log(`   - 当前版本: ${appStore.currentVersion}`);
    console.log(`   - 最新版本: ${appStore.latestVersion}`);
    console.log(`   - 获取到 ${appStore.releases.length} 个 Release。`);

  } catch (err) {
    console.error("[AboutPage] 4. fetchData 在 try 块中捕获到错误!", err);
    error.value = '无法获取版本信息。';
  } finally {
    console.log("[AboutPage] 5. fetchData 进入 finally 块，准备设置 isLoading = false。");
    isLoading.value = false;
  }
};

const renderMarkdown = (markdownText) => {
  if (!markdownText) return '';
  // +++ 核心修改：调用 marked.parse() 方法 +++
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
.sponsor-content {
  text-align: center;
}
.qr-code-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
</style>
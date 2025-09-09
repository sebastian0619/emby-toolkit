<template>
  <!-- 这个组件现在没有完整的 n-layout，因为它只是一个弹窗内容 -->
  <n-spin :show="loadingConfig">
    <n-space vertical :size="24">
      <n-card :bordered="false">
        <template #header>
          <span style="font-size: 1.2em; font-weight: bold;">媒体洗版规则</span>
        </template>
        <template #header-extra>
          <n-space align="center">
            <span>启用媒体洗版</span>
            <n-switch v-model:value="configModel.resubscribe_enabled" />
          </n-space>
        </template>
        <p style="margin-top: 0; color: #888;">
          在这里配置的规则，将用于“一键洗版全部”和“刷新媒体洗版状态”任务。
        </p>
      </n-card>

      <div class="settings-wrapper" :class="{ 'content-disabled': !configModel.resubscribe_enabled }">
        <n-grid cols="1" :y-gap="24" responsive="screen">
          <!-- 分辨率洗版设置 -->
          <n-gi>
            <n-card title="按分辨率洗版" :bordered="false">
              <template #header-extra>
                <n-switch v-model:value="configModel.resubscribe_resolution_enabled" :disabled="!configModel.resubscribe_enabled" />
              </template>
              <n-form-item label="洗版分辨率阈值 (宽度)" label-placement="top">
                <n-select
                  v-model:value="configModel.resubscribe_resolution_threshold"
                  :options="resolutionOptions"
                  :disabled="!configModel.resubscribe_enabled || !configModel.resubscribe_resolution_enabled"
                />
                <template #feedback>当媒体文件的视频宽度小于此值时，触发洗版。</template>
              </n-form-item>
            </n-card>
          </n-gi>
          <!-- ★★★ 质量洗版设置 ★★★ -->
          <n-gi>
            <n-card title="按质量洗版" :bordered="false">
              <template #header-extra>
                <n-switch v-model:value="configModel.resubscribe_quality_enabled" :disabled="!configModel.resubscribe_enabled" />
              </template>
              <n-form-item label="当文件名【不包含】以下任一关键词时触发洗版" label-placement="top">
                <!-- ▼▼▼ 核心修改：增加 tag 属性，允许用户自由输入 ▼▼▼ -->
                <n-select
                  v-model:value="configModel.resubscribe_quality_include"
                  multiple
                  tag 
                  filterable
                  placeholder="可选择或自由输入，按回车确认"
                  :options="qualityOptions"
                  :disabled="!configModel.resubscribe_enabled || !configModel.resubscribe_quality_enabled"
                />
                <template #feedback>系统会检查媒体文件名。如果文件名一个都匹配不上，就会被洗版。</template>
              </n-form-item>
            </n-card>
          </n-gi>

          <!-- 特效洗版设置 -->
          <n-gi>
            <n-card title="按特效洗版" :bordered="false">
              <template #header-extra>
                <n-switch v-model:value="configModel.resubscribe_effect_enabled" :disabled="!configModel.resubscribe_enabled" />
              </template>
              <n-form-item label="当文件名【不包含】以下任一关键词时触发洗版" label-placement="top">
                <!-- ▼▼▼ 核心修改：增加 tag 属性，允许用户自由输入 ▼▼▼ -->
                <n-select
                  v-model:value="configModel.resubscribe_effect_include"
                  multiple
                  tag
                  filterable
                  placeholder="可选择或自由输入，按回车确认"
                  :options="effectOptions"
                  :disabled="!configModel.resubscribe_enabled || !configModel.resubscribe_effect_enabled"
                />
                <template #feedback>系统会检查媒体文件名。如果一个都匹配不上，就会被洗版。</template>
              </n-form-item>
            </n-card>
          </n-gi>
          <!-- 音轨洗版设置 -->
          <n-gi>
            <n-card title="按音轨洗版" :bordered="false">
              <template #header-extra>
                <n-switch v-model:value="configModel.resubscribe_audio_enabled" :disabled="!configModel.resubscribe_enabled" />
              </template>
              <n-form-item label="当缺少以下音轨时触发洗版" label-placement="top">
                <n-select
                  v-model:value="configModel.resubscribe_audio_missing_languages"
                  multiple
                  tag
                  placeholder="例如: chi, zho, yue"
                  :options="languageOptions"
                  :disabled="!configModel.resubscribe_enabled || !configModel.resubscribe_audio_enabled"
                />
                <template #feedback>请填写音轨的3字母语言代码 (ISO 639-2)。常用的有 chi/zho (国语), yue (粤语), eng (英语)。</template>
              </n-form-item>
            </n-card>
          </n-gi>

          <!-- 字幕洗版设置 -->
          <n-gi>
            <n-card title="按字幕洗版" :bordered="false">
              <template #header-extra>
                <n-switch v-model:value="configModel.resubscribe_subtitle_enabled" :disabled="!configModel.resubscribe_enabled" />
              </template>
              <n-form-item label="当缺少以下字幕时触发洗版" label-placement="top">
                <n-select
                  v-model:value="configModel.resubscribe_subtitle_missing_languages"
                  multiple
                  tag
                  placeholder="例如: chi, zho"
                  :options="languageOptions"
                  :disabled="!configModel.resubscribe_enabled || !configModel.resubscribe_subtitle_enabled"
                />
                <template #feedback>请填写字幕的3字母语言代码。通常只需要关心 chi/zho (中字)。</template>
              </n-form-item>
            </n-card>
          </n-gi>
        </n-grid>
      </div>

      <n-button type="primary" @click="save" :loading="savingConfig" block size="large" style="margin-top: 24px;">
        保存规则
      </n-button>
    </n-space>
  </n-spin>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import axios from 'axios';
import { NCard, NSpace, NSwitch, NGrid, NGi, NFormItem, NSelect, NButton, useMessage, NSpin } from 'naive-ui';

const message = useMessage();
const configModel = ref({});
const savingConfig = ref(false);
const loadingConfig = ref(true);

// ★★★ 定义 emit，用于通知父组件（海报墙页面）关闭弹窗 ★★★
const emit = defineEmits(['saved']);

const resolutionOptions = ref([
  { label: '低于 4K (3840px)', value: 3840 },
  { label: '低于 1080p (1920px)', value: 1920 },
  { label: '低于 720p (1280px)', value: 1280 },
]);

const qualityOptions = ref([
  { label: 'Remux', value: 'remux' },
  { label: 'BluRay / 蓝光', value: 'bluray' },
  { label: 'WEB-DL', value: 'web-dl' },
  { label: 'UHD', value: 'uhd' },
  { label: 'BDRip', value: 'bdrip' },
  { label: 'HDTV', value: 'hdtv' },
]);

const effectOptions = ref([
  { label: 'HDR', value: 'hdr' },
  { label: 'Dolby Vision / DoVi', value: 'dovi' },
  { label: 'HDR10+', value: 'hdr10+' },
  { label: 'HLG', value: 'hlg' },
]);

const languageOptions = ref([
    { label: '国语 (chi/zho)', value: 'chi' },
    { label: '粤语 (yue)', value: 'yue' },
    { label: '英语 (eng)', value: 'eng' },
]);

const loadSettings = async () => {
  loadingConfig.value = true;
  try {
    const response = await axios.get('/api/resubscribe/settings');
    configModel.value = response.data;
  } catch (error) {
    message.error('加载洗版设置失败，请检查网络或后端日志。');
  } finally {
    loadingConfig.value = false;
  }
};

const save = async () => {
  savingConfig.value = true;
  try {
    await axios.post('/api/resubscribe/settings', configModel.value);
    message.success('媒体洗版规则已成功保存！');
    // ★★★ 保存成功后，触发 'saved' 事件 ★★★
    emit('saved');
  } catch (error) {
    message.error('保存失败，请检查后端日志。');
  } finally {
    savingConfig.value = false;
  }
};

onMounted(() => {
  loadSettings();
});
</script>

<style scoped>
.settings-wrapper {
  transition: opacity 0.3s ease;
}
.content-disabled {
  opacity: 0.5;
  pointer-events: none;
}
</style>
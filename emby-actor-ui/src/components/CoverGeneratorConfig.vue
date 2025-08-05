<template>
  <n-layout content-style="padding: 24px;">
    <n-spin :show="isLoading">
      <div class="cover-generator-config">
        <n-page-header>
          <template #title>媒体库封面生成</template>
          <template #extra>
            <n-space>
              <n-button @click="runGenerateAllTask" :loading="isGenerating">
                <template #icon><n-icon :component="ImagesIcon" /></template>
                立即生成所有媒体库封面
              </n-button>
              <n-button type="primary" @click="saveConfig" :loading="isSaving">
                <template #icon><n-icon :component="SaveIcon" /></template>
                保存设置
              </n-button>
            </n-space>
          </template>
        </n-page-header>

        <!-- ★★★ 核心修改：使用 n-grid 重新排版 ★★★ -->
        <n-card title="基础设置" style="margin-top: 24px;">
          <n-grid :cols="4" :x-gap="24" responsive="screen">
            <!-- 第一列 -->
            <n-gi>
              <n-form-item label="启用">
                <n-switch v-model:value="configData.enabled" />
              </n-form-item>
            </n-gi>
            <!-- 第二列 -->
            <n-gi>
              <n-form-item label="监控新入库">
                <n-switch v-model:value="configData.transfer_monitor" />
                <template #feedback>新媒体入库后自动更新所在库封面</template>
              </n-form-item>
            </n-gi>
            <!-- 第三列 -->
            <n-gi>
              <n-form-item label="在封面上显示数量">
                <n-switch v-model:value="configData.show_item_count" />
                <template #feedback>在封面右下角显示媒体项总数</template>
              </n-form-item>
            </n-gi>
            <!-- 第四列 -->
            <n-gi>
              <n-form-item label="封面图片来源排序">
                <n-select v-model:value="configData.sort_by" :options="sortOptions" />
              </n-form-item>
            </n-gi>
            <!-- 另起一行，占据所有列 -->
            <n-gi :span="3">
              <n-form-item label="选择要【忽略】的媒体库">
                <n-checkbox-group v-model:value="configData.exclude_libraries" class="exclude-group">
                  <n-grid :y-gap="8" :cols="6" responsive="screen">
                    <n-gi v-for="lib in libraryOptions" :key="lib.value">
                      <n-checkbox :value="lib.value" :label="lib.label" />
                    </n-gi>
                  </n-grid>
                </n-checkbox-group>
                <template #feedback>勾选的媒体库将【不会】被处理</template>
              </n-form-item>
            </n-gi>
          </n-grid>
        </n-card>

        <!-- ... 其余的 n-card 和 n-tabs 保持不变 ... -->
        <n-card style="margin-top: 24px;">
          <n-tabs v-model:value="configData.tab" type="line" animated>
            <n-tab-pane name="style-tab" tab="封面风格">
              <n-radio-group v-model:value="configData.cover_style" name="cover-style-group">
                <n-grid :cols="3" :x-gap="16" :y-gap="16" responsive="screen">
                  <n-gi v-for="style in styles" :key="style.value">
                    <n-card class="style-card">
                      <template #cover><img :src="style.src" class="style-img" /></template>
                      <n-radio :value="style.value" :label="style.title" />
                    </n-card>
                  </n-gi>
                </n-grid>
              </n-radio-group>
            </n-tab-pane>

            <n-tab-pane name="title-tab" tab="封面标题">
              <n-form-item label="中英标题配置 (YAML格式)">
                <n-input
                  v-model:value="configData.title_config"
                  type="textarea"
                  :autosize="{ minRows: 10 }"
                  placeholder="媒体库名称:\n  - 中文标题\n  - 英文标题"
                />
              </n-form-item>
            </n-tab-pane>

            <n-tab-pane name="single-tab" tab="单图风格设置">
              <n-alert type="info" :bordered="false" style="margin-bottom: 20px;">
                若字体无法下载，建议在主程序的网络设置中配置GitHub代理，或手动下载字体后填写本地路径。
              </n-alert>
              <n-grid :cols="2" :x-gap="24" :y-gap="12" responsive="screen">
                <n-gi>
                  <n-form-item label="中文字体（本地路径）">
                    <n-input v-model:value="configData.zh_font_path_local" placeholder="留空使用预设字体" />
                    <template #feedback>本地路径优先于下载链接</template>
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="英文字体（本地路径）">
                    <n-input v-model:value="configData.en_font_path_local" placeholder="留空使用预设字体" />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="中文字体（下载链接）">
                    <n-input v-model:value="configData.zh_font_url" placeholder="留空使用预设字体" />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="英文字体（下载链接）">
                    <n-input v-model:value="configData.en_font_url" placeholder="留空使用预设字体" />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="中文字体大小比例">
                    <n-input-number v-model:value="configData.zh_font_size" :step="0.1" placeholder="1.0" />
                    <template #feedback>相对于预设尺寸的比例，1为原始大小</template>
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="英文字体大小比例">
                    <n-input-number v-model:value="configData.en_font_size" :step="0.1" placeholder="1.0" />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="背景模糊程度">
                    <n-input-number v-model:value="configData.blur_size" placeholder="50" />
                    <template #feedback>数字越大越模糊，默认 50</template>
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="背景颜色混合占比">
                    <n-input-number v-model:value="configData.color_ratio" :step="0.1" placeholder="0.8" />
                     <template #feedback>颜色所占的比例，0-1，默认 0.8</template>
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="优先使用海报图">
                    <n-switch v-model:value="configData.single_use_primary" />
                    <template #feedback>不启用则优先使用背景图</template>
                  </n-form-item>
                </n-gi>
              </n-grid>
            </n-tab-pane>

            <n-tab-pane name="multi-1-tab" tab="多图风格设置">
              <n-grid :cols="2" :x-gap="24" :y-gap="12" responsive="screen">
                <n-gi :span="2">
                  <n-alert type="info" :bordered="false">
                    此页为“多图风格1”的专属设置。
                  </n-alert>
                </n-gi>
                <n-gi>
                  <n-form-item label="中文字体（本地路径）">
                    <n-input v-model:value="configData.zh_font_path_multi_1_local" placeholder="留空使用预设字体" :disabled="configData.multi_1_use_main_font" />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="英文字体（本地路径）">
                    <n-input v-model:value="configData.en_font_path_multi_1_local" placeholder="留空使用预设字体" :disabled="configData.multi_1_use_main_font" />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="中文字体（下载链接）">
                    <n-input v-model:value="configData.zh_font_url_multi_1" placeholder="留空使用预设字体" :disabled="configData.multi_1_use_main_font" />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="英文字体（下载链接）">
                    <n-input v-model:value="configData.en_font_url_multi_1" placeholder="留空使用预设字体" :disabled="configData.multi_1_use_main_font" />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="中文字体大小比例">
                    <n-input-number v-model:value="configData.zh_font_size_multi_1" :step="0.1" placeholder="1.0" />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="英文字体大小比例">
                    <n-input-number v-model:value="configData.en_font_size_multi_1" :step="0.1" placeholder="1.0" />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="背景模糊程度">
                    <n-input-number v-model:value="configData.blur_size_multi_1" placeholder="50" :disabled="!configData.multi_1_blur" />
                    <template #feedback>需启用模糊背景</template>
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="背景颜色混合占比">
                    <n-input-number v-model:value="configData.color_ratio_multi_1" :step="0.1" placeholder="0.8" :disabled="!configData.multi_1_blur" />
                    <template #feedback>需启用模糊背景</template>
                  </n-form-item>
                </n-gi>
                 <n-gi :span="2">
                  <n-space>
                    <n-form-item label="启用模糊背景">
                      <n-switch v-model:value="configData.multi_1_blur" />
                      <template #feedback>不启用则使用纯色渐变背景</template>
                    </n-form-item>
                    <n-form-item label="使用单图风格字体">
                      <n-switch v-model:value="configData.multi_1_use_main_font" />
                       <template #feedback>启用后将忽略本页的字体路径和链接设置</template>
                    </n-form-item>
                    <n-form-item label="优先使用海报图">
                      <n-switch v-model:value="configData.multi_1_use_primary" />
                       <template #feedback>多图风格建议开启</template>
                    </n-form-item>
                  </n-space>
                </n-gi>
              </n-grid>
            </n-tab-pane>
            
            <n-tab-pane name="others-tab" tab="其他设置">
              <n-grid :cols="2" :x-gap="24">
                <n-gi>
                  <n-form-item label="自定义图片目录（可选）">
                    <n-input v-model:value="configData.covers_input" placeholder="/path/to/custom/images" />
                  </n-form-item>
                </n-gi>
                <n-gi>
                  <n-form-item label="封面另存目录（可选）">
                    <n-input v-model:value="configData.covers_output" placeholder="/path/to/save/covers" />
                  </n-form-item>
                </n-gi>
              </n-grid>
            </n-tab-pane>
          </n-tabs>
        </n-card>
      </div>
    </n-spin>
  </n-layout>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import axios from 'axios';
import { useMessage, NLayout, NPageHeader, NButton, NIcon, NCard, NGrid, NGi, NFormItem, NSwitch, NSelect, NTabs, NCheckboxGroup, NCheckbox, NSpin, NSpace } from 'naive-ui';
import { SaveOutline as SaveIcon, ImagesOutline as ImagesIcon } from '@vicons/ionicons5';

// 导入静态图片数据
import { single_1, single_2, multi_1 } from '../assets/cover_styles/images.js';

const message = useMessage();
const isLoading = ref(true);
const isSaving = ref(false);
const isGenerating = ref(false);
const configData = ref({});

const libraryOptions = ref([]);

const sortOptions = [
  { label: "最新添加", value: "Latest" },
  { label: "随机", value: "Random" },
];

const styles = [
  { title: "单图 1", value: "single_1", src: single_1 },
  { title: "单图 2", value: "single_2", src: single_2 },
  { title: "多图 1", value: "multi_1", src: multi_1 }
];

const fetchConfig = async () => {
  isLoading.value = true;
  try {
    const response = await axios.get('/api/config/cover_generator');
    configData.value = response.data;
  } catch (error) {
    message.error('加载封面生成器配置失败。');
  } finally {
    isLoading.value = false;
  }
};

const fetchLibraryOptions = async () => {
  try {
    const response = await axios.get('/api/config/cover_generator/libraries');
    libraryOptions.value = response.data;
  } catch (error) {
    message.error('获取媒体库列表失败，请检查后端。');
  }
};

const saveConfig = async () => {
  isSaving.value = true;
  try {
    await axios.post('/api/config/cover_generator', configData.value);
    message.success('配置已成功保存！');
  } catch (error) {
    message.error('保存配置失败。');
  } finally {
    isSaving.value = false;
  }
};

const runGenerateAllTask = async () => {
  isGenerating.value = true;
  try {
    await axios.post('/api/tasks/run', { task_name: 'generate-all-covers' });
    message.success('已成功触发“立即生成所有媒体库封面”任务，请在任务队列中查看进度。');
  } catch (error) {
    message.error('触发任务失败，请检查后端日志。');
  } finally {
    isGenerating.value = false;
  }
};

onMounted(() => {
  fetchConfig();
  fetchLibraryOptions();
});
</script>

<style scoped>
.style-card {
  cursor: pointer;
  text-align: center;
}
.style-img {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  border-bottom: 1px solid #eee;
}
.n-radio {
  margin-top: 12px;
  justify-content: center;
  width: 100%;
}
</style>
<template>
  <n-layout content-style="padding: 24px;">
    <n-spin :show="isLoading">
      <div class="cover-generator-config">
        <n-page-header>
          <template #title>媒体库封面生成</template>
          <template #extra>
            <!-- ★★★ 新增：刷新缓存按钮 ★★★ -->
            <!--<n-button @click="runCacheTask" :loading="isCaching" style="margin-right: 12px;">
              <template #icon><n-icon :component="SyncIcon" /></template>
              刷新路径缓存
            </n-button>-->
            <n-button type="primary" @click="saveConfig" :loading="isSaving">
              <template #icon><n-icon :component="SaveIcon" /></template>
              保存设置
            </n-button>
          </template>
        </n-page-header>

        <n-card title="基础设置" style="margin-top: 24px;">
          <n-grid :cols="4" :x-gap="24" responsive="screen">
            <n-gi>
              <n-form-item label="启用插件">
                <n-switch v-model:value="configData.enabled" />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="立即运行一次">
                <n-switch v-model:value="configData.onlyonce" />
                <template #feedback>保存后将立即为所有媒体库更新封面</template>
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="监控新入库">
                <n-switch v-model:value="configData.transfer_monitor" />
                <template #feedback>新媒体入库后自动更新所在库封面</template>
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="入库延迟（秒）">
                <n-input-number v-model:value="configData.delay" />
              </n-form-item>
            </n-gi>
            <n-gi :span="2">
              <n-form-item label="媒体服务器">
                <n-select
                  v-model:value="configData.selected_servers"
                  multiple filterable placeholder="选择要生成封面的服务器"
                  :options="serverOptions"
                />
              </n-form-item>
            </n-gi>
            <n-gi>
              <!-- ★★★ 修改：更新排序选项 ★★★ -->
              <n-form-item label="封面图片来源排序">
                <n-select v-model:value="configData.sort_by" :options="sortOptions" />
              </n-form-item>
            </n-gi>
            <n-gi>
              <n-form-item label="定时更新（Cron）">
                <n-input v-model:value="configData.cron" placeholder="例如: 0 3 * * *" />
              </n-form-item>
            </n-gi>
            <n-gi :span="4">
               <n-form-item label="忽略的媒体库">
                <n-select
                  v-model:value="configData.exclude_libraries"
                  multiple filterable placeholder="选择后将不为这些库生成封面"
                  :options="libraryOptions"
                  :disabled="!configData.selected_servers || configData.selected_servers.length === 0"
                />
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
import { useMessage, NLayout, NPageHeader, NButton, NIcon, NCard, NGrid, NGi, NFormItem, NSwitch, NInputNumber, NSelect, NInput, NTabs, NTabPane, NRadioGroup, NRadio, NSpin, NSpace } from 'naive-ui';
// ★★★ 新增：导入新图标 ★★★
import { SaveOutline as SaveIcon, SyncOutline as SyncIcon } from '@vicons/ionicons5';

// 导入静态图片数据
import { single_1, single_2, multi_1 } from '../assets/cover_styles/images.js';


const message = useMessage();
const isLoading = ref(true);
const isSaving = ref(false);
// ★★★ 新增：缓存按钮的加载状态 ★★★
const isCaching = ref(false);
const configData = ref({});

const serverOptions = ref([]);
const libraryOptions = ref([]);

// ★★★ 修改：提供更完整、更准确的排序选项 ★★★
// 注意：这里的 value 需要和后端 __get_valid_items_from_library 函数中的 self._sort_by 判断条件完全一致
const sortOptions = [
  { label: "随机", value: "Random" },
  { label: "最新添加", value: "Latest" },
  // 如果您未来在后端实现了按发行日期排序，可以取消下面的注释
  // { label: "最新发行", value: "PremiereDate" }
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

const fetchSelectOptions = async () => {
  try {
    const [serverRes, libraryRes] = await Promise.all([
      axios.get('/api/config/cover_generator/servers'),
      axios.get('/api/config/cover_generator/libraries')
    ]);
    serverOptions.value = serverRes.data;
    libraryOptions.value = libraryRes.data;
  } catch (error) {
    message.error('获取服务器或媒体库列表失败，请检查后端。');
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

// ★★★ 新增：执行刷新缓存任务的函数 ★★★
const runCacheTask = async () => {
  isCaching.value = true;
  try {
    // ★★★ 核心修复：在 post 请求中添加一个空的 JSON 对象作为第二个参数 ★★★
    await axios.post('/api/config/cover_generator/run-cache-task', { task_name: 'update-library-cache' });
    message.success('已成功触发“刷新路径缓存”任务，请稍后在任务队列中查看结果。');
  } catch (error) {
    // 现在如果还报错，可以更详细地提示
    const errorMsg = error.response?.data?.error || '触发缓存任务失败，请检查后端日志。';
    message.error(errorMsg);
  } finally {
    isCaching.value = false;
  }
};

onMounted(() => {
  fetchConfig();
  fetchSelectOptions();
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
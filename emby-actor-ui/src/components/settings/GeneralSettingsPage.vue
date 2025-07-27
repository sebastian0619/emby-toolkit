<!-- src/components/settings/GeneralSettingsPage.vue -->
<template>
  <n-layout content-style="padding: 24px;">
  <n-space vertical :size="24" style="margin-top: 15px;">
  <div v-if="configModel">
  <n-form
    ref="formRef"  
    :rules="formRules"
    v-if="configModel"
    @submit.prevent="save"
    label-placement="left"
    label-width="auto"
    label-align="right"
    :model="configModel"
  >
    <n-grid cols="1 m:2" :x-gap="24" :y-gap="24" responsive="screen">
      <!-- ########## 左侧列 ########## -->
      <n-gi>
        <n-space vertical :size="24">
          <!-- 卡片: 基础设置 -->
          <n-card title="基础设置" size="small" class="glass-section">
            <n-form-item-grid-item label="处理项目间的延迟 (秒)" path="delay_between_items_sec">
              <n-input-number v-model:value="configModel.delay_between_items_sec" :min="0" :step="0.1" placeholder="例如: 0.5"/>
            </n-form-item-grid-item>
            
            <n-form-item-grid-item label="豆瓣API默认冷却时间 (秒)" path="api_douban_default_cooldown_seconds">
              <n-input-number v-model:value="configModel.api_douban_default_cooldown_seconds" :min="0.1" :step="0.1" placeholder="例如: 1.0"/>
            </n-form-item-grid-item>

            <n-form-item-grid-item label="需手动处理的最低评分阈值" path="min_score_for_review">
              <n-input-number v-model:value="configModel.min_score_for_review" :min="0.0" :max="10" :step="0.1" placeholder="例如: 6.0"/>
              <template #feedback>
                <n-text depth="3" style="font-size:0.8em;">处理质量评分低于此值的项目将进入待复核列表。</n-text>
              </template>
            </n-form-item-grid-item>
            <n-form-item-grid-item label="最大处理演员数" path="max_actors_to_process">
            <n-input-number 
              v-model:value="configModel.max_actors_to_process" 
              :min="10" 
              :step="10" 
              placeholder="建议 30-100"
            />
            <template #feedback>
              <n-text depth="3" style="font-size:0.8em;">设置最终写入元数据的演员数量上限，避免列表过长。</n-text>
            </template>
          </n-form-item-grid-item>
          <n-form-item-grid-item label="为角色名添加前缀" path="actor_role_add_prefix">
            <n-switch v-model:value="configModel.actor_role_add_prefix" />
            <template #feedback>
              <n-text depth="3" style="font-size:0.8em;">
                开启后，角色名前会加上“饰 ”或“配 ”，例如“饰 凌凌漆”。关闭则直接显示角色名。
              </n-text>
            </template>
          </n-form-item-grid-item>
            <n-form-item-grid-item label="更新后刷新 Emby 媒体项">
              <n-switch v-model:value="configModel.refresh_emby_after_update" />
            </n-form-item-grid-item>
            <n-form-item label="处理分集" path="process_episodes">
              <n-switch v-model:value="configModel.process_episodes" />
              <template #feedback>开启后，处理电视剧时会为每一季/每一集生成单独的元数据文件。</template>
            </n-form-item>
            <n-form-item label="同步图片" path="sync_images">
              <n-switch v-model:value="configModel.sync_images" />
              <template #feedback>开启后，处理媒体时会下载海报、横幅图等图片文件。</template>
            </n-form-item>
          </n-card>
          
          <!-- 卡片: 数据源与 API -->
          <n-card title="数据源与 API" size="small" class="glass-section">
            <n-form-item label="本地数据源路径" path="local_data_path" required>
              <n-input v-model:value="configModel.local_data_path" placeholder="神医TMDB缓存目录 (cache和override的上层)" />
            </n-form-item>
            <n-form-item label="TMDB API Key" path="tmdb_api_key">
              <n-input type="password" show-password-on="mousedown" v-model:value="configModel.tmdb_api_key" placeholder="输入你的 TMDB API Key" />
            </n-form-item>

            <n-form-item label="豆瓣登录 Cookie" path="douban_cookie">
              <n-input
                type="password"
                show-password-on="mousedown"
                v-model:value="configModel.douban_cookie"
                placeholder="从浏览器开发者工具中获取"
              />
              <template #feedback>
                <n-text depth="3" style="font-size:0.8em;">
                  非必要不用配置，当日志频繁出现“豆瓣API请求失败: 需要登录...”的提示时再配置。
                </n-text>
              </template>
            </n-form-item>
          </n-card>
          <n-card title="日志配置（更改后重启生效）" size="small" class="glass-section">
            <n-form-item-grid-item label="单个日志文件大小 (MB)" path="log_rotation_size_mb">
              <n-input-number 
                v-model:value="configModel.log_rotation_size_mb" 
                :min="1" 
                :step="1" 
                placeholder="例如: 5"
              />
              <template #feedback>
                <n-text depth="3" style="font-size:0.8em;">设置 app.log 文件的最大体积，超限后会轮转。</n-text>
              </template>
            </n-form-item-grid-item>

            <n-form-item-grid-item label="日志备份数量" path="log_rotation_backup_count">
              <n-input-number 
                v-model:value="configModel.log_rotation_backup_count" 
                :min="1" 
                :step="1" 
                placeholder="例如: 10"
              />
              <template #feedback>
                <n-text depth="3" style="font-size:0.8em;">保留最近的日志文件数量 (app.log.1, app.log.2 ...)。</n-text>
              </template>
            </n-form-item-grid-item>
          </n-card>
        </n-space>
      </n-gi>

      <!-- ########## 右侧列 ########## -->
      <n-gi>
        <n-space vertical :size="24">
          <!-- 卡片: 传统翻译引擎 -->
          <n-card title="传统翻译引擎" size="small" class="glass-section">
            <n-form-item label="翻译引擎顺序" path="translator_engines_order">
              <template #feedback>可拖动调整顺序，点击添加新的翻译引擎。</template>
              <draggable
                v-model="configModel.translator_engines_order"
                item-key="value"
                tag="div"
                class="engine-list"
                handle=".drag-handle"
                animation="300"
              >
                <template #item="{ element: engineValue, index }">
                  <n-tag :key="engineValue" type="primary" closable class="engine-tag" @close="removeEngine(index)">
                    <n-icon :component="DragHandleIcon" class="drag-handle" />
                    {{ getEngineLabel(engineValue) }}
                  </n-tag>
                </template>
              </draggable>
              <n-select
                v-if="unselectedEngines.length > 0"
                placeholder="点击添加新的翻译引擎..."
                :options="unselectedEngines"
                @update:value="addEngine"
                style="margin-top: 12px;"
              />
            </n-form-item>
          </n-card>

          <!-- 卡片: AI 翻译设置 -->
          <n-card title="AI 翻译设置" size="small" class="glass-section">
            <template #header-extra>
              <n-space align="center">
                <n-switch v-model:value="configModel.ai_translation_enabled" />
                <a
                  href="https://cloud.siliconflow.cn/i/GXIrubbL"
                  target="_blank"
                  style="font-size: 0.85em; margin-left: 8px; color: var(--n-primary-color); text-decoration: underline;"
                >
                  注册硅基流动，新人送2000万tokens
                </a>
              </n-space>
            </template>
            <div class="ai-settings-wrapper" :class="{ 'content-disabled': !configModel.ai_translation_enabled }">
              <n-form-item label="AI翻译模式" path="ai_translation_mode">
                <n-radio-group 
                  v-model:value="configModel.ai_translation_mode" 
                  name="ai_translation_mode"
                  :disabled="!configModel.ai_translation_enabled"
                >
                  <n-space>
                    <n-radio value="fast">
                      翻译模式 (速度优先)
                    </n-radio>
                    <n-radio value="quality">
                      顾问模式 (质量优先)
                    </n-radio>
                  </n-space>
                </n-radio-group>
                <template #feedback>
                  <n-text depth="3" style="font-size:0.8em;">
                    <b>翻译模式：</b>纯翻译，全局共享缓存，速度快成本低。
                    <br>
                    <b>顾问模式：</b>作为“影视顾问”，结合上下文翻译，准确率更高，但无缓存，专片专译，耗时且成本高。
                  </n-text>
                </template>
              </n-form-item>
              <n-form-item label="AI 服务商" path="ai_provider">
                <n-select 
                  v-model:value="configModel.ai_provider" 
                  :options="aiProviderOptions" 
                  :disabled="!configModel.ai_translation_enabled"
                />
              </n-form-item>
              <n-form-item label="API Key" path="ai_api_key">
                <n-input 
                  type="password" 
                  show-password-on="mousedown" 
                  v-model:value="configModel.ai_api_key" 
                  placeholder="输入你的 API Key" 
                  :disabled="!configModel.ai_translation_enabled"
                />
              </n-form-item>
              <n-form-item label="模型名称" path="ai_model_name">
                <n-input 
                  v-model:value="configModel.ai_model_name" 
                  placeholder="例如: gpt-3.5-turbo, glm-4, gemini-pro" 
                  :disabled="!configModel.ai_translation_enabled"
                />
              </n-form-item>
              <n-form-item label="API Base URL (可选)" path="ai_base_url">
                <n-input 
                  v-model:value="configModel.ai_base_url" 
                  placeholder="用于代理或第三方兼容服务" 
                  :disabled="!configModel.ai_translation_enabled"
                />
              </n-form-item>
            </div>
          </n-card>
          <n-card title="MoviePilot 订阅服务" size="small" class="glass-section">
            <n-form-item-grid-item label="MoviePilot URL" path="moviepilot_url">
              <n-input v-model:value="configModel.moviepilot_url" placeholder="例如: http://192.168.1.100:3000"/>
            </n-form-item-grid-item>
            <n-form-item-grid-item label="用户名" path="moviepilot_username">
              <n-input v-model:value="configModel.moviepilot_username" placeholder="输入 MoviePilot 的登录用户名"/>
            </n-form-item-grid-item>
            <n-form-item-grid-item label="密码" path="moviepilot_password">
              <n-input type="password" show-password-on="mousedown" v-model:value="configModel.moviepilot_password" placeholder="输入 MoviePilot 的登录密码"/>
            </n-form-item-grid-item>
            
            <n-divider title-placement="left" style="margin-top: 20px; margin-bottom: 20px;">
              智能订阅设置
            </n-divider>

            <n-form-item-grid-item label="启用智能订阅" path="autosub_enabled">
              <n-switch v-model:value="configModel.autosub_enabled" />
              <template #feedback>
                <n-text depth="3" style="font-size:0.8em;">
                  总开关。开启后，智能订阅定时任务才会真正执行订阅操作。
                </n-text>
              </template>
            </n-form-item-grid-item>

          </n-card>
          
        </n-space>
      </n-gi>
    </n-grid>

    <!-- 页面底部的统一保存按钮 -->
    <n-button type="primary" attr-type="submit" :loading="savingConfig" block size="large" style="margin-top: 24px;">
      保存所有设置
    </n-button>
  </n-form>
  
  <n-alert v-else-if="configError" title="加载配置失败" type="error">
    {{ configError }}
  </n-alert>

  <div v-else>
    正在加载配置...
  </div>
  </div>
  </n-space>
  </n-layout>
</template>

<script setup>
import { ref, computed } from 'vue';
import draggable from 'vuedraggable';
import { 
  NCard, NForm, NFormItem, NInputNumber, NSwitch, NButton, NGrid, NGi, 
  NSpin, NAlert, NInput, NSelect, NSpace, NTag, NIcon, useMessage,
  NFormItemGridItem
} from 'naive-ui';
import { MoveOutline as DragHandleIcon } from '@vicons/ionicons5';
import { useConfig } from '../../composables/useConfig.js';
import { useAuthStore } from '../../stores/auth';
const formRef = ref(null); // 1. 创建一个表单引用
const formRules = {      // 2. 定义验证规则
  local_data_path: {
    required: true,
    message: '本地数据源路径是必填项，不能为空！',
    trigger: ['input', 'blur'] // 当输入或失去焦点时触发验证
  }
};
const { configModel, loadingConfig, savingConfig, configError, handleSaveConfig } = useConfig();
const authStore = useAuthStore();
const message = useMessage();

// --- 保存逻辑 ---
async function save() {
  try {
    // 在这里调用验证！
    await formRef.value?.validate();

    // 如果验证通过 (没有抛出错误)，则继续执行保存逻辑
    const success = await handleSaveConfig();
    if (success) {
      message.success('所有设置已成功保存！');
    } else {
      message.error(configError.value || '配置保存失败，请检查后端日志。');
    }
  } catch (errors) {
    // 如果验证失败，Naive UI 会自动在表单项下显示错误信息
    // 我们可以在控制台打印错误，并提示用户
    console.log('表单验证失败:', errors);
    message.error('请检查表单中的必填项或错误项！');
  }
}
loadingConfig.value = false;
// --- 翻译引擎逻辑 ---
const availableTranslatorEngines = ref([
  { label: '必应 (Bing)', value: 'bing' },
  { label: '谷歌 (Google)', value: 'google' },
  { label: '百度 (Baidu)', value: 'baidu' },
  { label: '阿里 (Alibaba)', value: 'alibaba' },
  { label: '有道 (Youdao)', value: 'youdao' },
  { label: '腾讯 (Tencent)', value: 'tencent' },
]);

const getEngineLabel = (value) => {
  const engine = availableTranslatorEngines.value.find(e => e.value === value);
  return engine ? engine.label : value;
};

const unselectedEngines = computed(() => {
  if (!configModel.value?.translator_engines_order) return availableTranslatorEngines.value;
  const selectedValues = new Set(configModel.value.translator_engines_order);
  return availableTranslatorEngines.value.filter(engine => !selectedValues.has(engine.value));
});

const addEngine = (value) => {
  if (!configModel.value.translator_engines_order) {
    configModel.value.translator_engines_order = [];
  }
  if (value && !configModel.value.translator_engines_order.includes(value)) {
    configModel.value.translator_engines_order.push(value);
  }
};

const removeEngine = (index) => {
  configModel.value.translator_engines_order.splice(index, 1);
};

// --- AI 服务商逻辑 ---
// ✨✨✨ 核心修改在这里 ✨✨✨
const aiProviderOptions = ref([
  { label: 'OpenAI (及兼容服务)', value: 'openai' },
  { label: '智谱AI (ZhipuAI)', value: 'zhipuai' },
  { label: 'Google Gemini', value: 'gemini' }, // <-- 新增这一行
]);
// ✨✨✨ 修改结束 ✨✨✨

</script>

<style scoped>
/* 禁用AI设置时的遮罩效果 */
.ai-settings-wrapper {
  transition: opacity 0.3s ease;
}
.content-disabled {
  opacity: 0.6;
}

/* 翻译引擎标签样式 */
.engine-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.engine-tag {
  cursor: grab;
}
.engine-tag:active {
  cursor: grabbing;
}
.drag-handle {
  margin-right: 6px;
  vertical-align: -0.15em;
}
</style>
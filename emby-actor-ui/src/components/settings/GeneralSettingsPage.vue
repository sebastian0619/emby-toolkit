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
          <n-card :bordered="false" class="dashboard-card">
            <template #header>
              <span class="card-title">基础设置</span>
            </template>
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
            <n-form-item label="自动锁定演员表" path="auto_lock_cast_after_update">
              <!-- ★★★ 修改点：添加 :disabled 属性 ★★★ -->
              <n-switch 
                v-model:value="configModel.auto_lock_cast_after_update" 
                :disabled="!configModel.refresh_emby_after_update"
              />
              <template #feedback>【酌情开启】开启后，处理完演员表后，会自动将该项目的“演员”字段锁定，防止被后续刷新操作覆盖，也可能会造成新增的演员来不及刷新。</template>
            </n-form-item>
          </n-card>
          
          <!-- 卡片: 数据源与 API -->
          <n-card :bordered="false" class="dashboard-card">
            <template #header>
              <span class="card-title">数据源与API</span>
            </template>
            <n-form-item label="本地数据源路径" path="local_data_path">
              <n-input v-model:value="configModel.local_data_path" placeholder="神医TMDB缓存目录 (cache和override的上层)" />
            </n-form-item>
            <n-form-item label="TMDB API Key" path="tmdb_api_key">
              <n-input type="password" show-password-on="mousedown" v-model:value="configModel.tmdb_api_key" placeholder="输入你的 TMDB API Key" />
            </n-form-item>
            <n-form-item label="GitHub 个人访问令牌" path="github_token">
              <n-input
                type="password"
                show-password-on="mousedown"
                v-model:value="configModel.github_token"
                placeholder="可选，用于提高API请求频率限制"
              />
              <template #feedback>
                <n-text depth="3" style="font-size:0.8em;">
                  <a
                  href="https://github.com/settings/tokens/new"
                  target="_blank"
                  style="font-size: 1.3em; margin-left: 8px; color: var(--n-primary-color); text-decoration: underline;"
                >
                  免费申请GithubTOKEN
                </a>
                </n-text>
              </template>
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
            <!-- ★★★ 分割线: 网络代理 ★★★ -->
            <n-divider title-placement="left" style="margin-top: 20px; margin-bottom: 20px;">
              网络代理
            </n-divider>

            <n-form-item-grid-item label="启用网络代理" path="network_proxy_enabled">
              <n-switch v-model:value="configModel.network_proxy_enabled" />
              <template #feedback>
                <n-text depth="3" style="font-size:0.8em;">
                  为 TMDb 等外部API请求启用 HTTP/HTTPS 代理。
                </n-text>
              </template>
            </n-form-item-grid-item>

            <n-form-item-grid-item label="HTTP 代理地址" path="network_http_proxy_url">
              <n-input-group>
                <n-input
                  v-model:value="configModel.network_http_proxy_url"
                  placeholder="例如: http://127.0.0.1:7890"
                  :disabled="!configModel.network_proxy_enabled"
                />
                <n-button 
                  type="primary" 
                  ghost 
                  @click="testProxy" 
                  :loading="isTestingProxy"
                  :disabled="!configModel.network_proxy_enabled || !configModel.network_http_proxy_url"
                >
                  测试连接
                </n-button>
              </n-input-group>
              <template #feedback>
                <n-text depth="3" style="font-size:0.8em;">
                  请填写完整的代理 URL，支持 http 和 https。
                </n-text>
              </template>
            </n-form-item-grid-item>
          </n-card>
          <n-card :bordered="false" class="dashboard-card">
            <template #header>
              <span class="card-title">日志配置</span>
            </template>
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
          
          <n-card :bordered="false" class="dashboard-card">
            <template #header>
              <span class="card-title">数据管理</span>
            </template>
            <n-space vertical>
              <n-space align="center">
                <n-button @click="showExportModal" :loading="isExporting" class="action-button">
                  <template #icon><n-icon :component="ExportIcon" /></template>
                  导出数据
                </n-button>
                <n-upload
                  :custom-request="handleCustomImportRequest"
                  :show-file-list="false"
                  accept=".json"
                >
                  <n-button :loading="isImporting" class="action-button">
                    <template #icon><n-icon :component="ImportIcon" /></template>
                    导入数据
                  </n-button>
                </n-upload>
              </n-space>
              <p class="description-text">
                <b>导出：</b>将数据库中的一个或多个表备份为 JSON 文件。<br>
                <b>导入：</b>从 JSON 备份文件中恢复数据，支持“共享合并”或“本地恢复”模式。
              </p>
            </n-space>
          </n-card>

        </n-space>
      </n-gi>

      <!-- ########## 右侧列 ########## -->
      <n-gi>
        <n-space vertical :size="24">
          <n-card :bordered="false" class="dashboard-card">
            <template #header>
              <span class="card-title">Emby设置</span>
            </template>
            <!-- Part 1: Emby 连接设置 -->
            <n-form-item-grid-item label="Emby 服务器 URL" path="emby_server_url">
              <n-input v-model:value="configModel.emby_server_url" placeholder="例如: http://localhost:8096" />
            </n-form-item-grid-item>
            <n-form-item-grid-item label="Emby API Key" path="emby_api_key">
              <n-input v-model:value="configModel.emby_api_key" type="password" show-password-on="click" placeholder="输入你的 Emby API Key" />
            </n-form-item-grid-item>
            <n-form-item-grid-item label="Emby 用户 ID" :rule="embyUserIdRule" path="emby_user_id">
              <n-input v-model:value="configModel.emby_user_id" placeholder="请输入32位的用户ID" />
              <template #feedback>
                <div v-if="isInvalidUserId" style="color: #e88080; font-size: 12px;">
                  格式错误！ID应为32位字母和数字。
                </div>
                <div v-else style="font-size: 12px; color: #888;">
                  提示：请从 Emby 后台用户管理页的地址栏复制 userId。
                </div>
              </template>
            </n-form-item-grid-item>

            <!-- 分割线: 媒体库 -->
            <n-divider title-placement="left" style="margin-top: 20px; margin-bottom: 20px;">
              选择要处理的媒体库
            </n-divider>

            <!-- ★★★ FIX: 修复后的“处理媒体库”选择 ★★★ -->
            <n-form-item-grid-item :span="24" label-placement="top" style="margin-top: -10px;">
              <n-spin :show="loadingLibraries">
                <n-checkbox-group v-model:value="configModel.libraries_to_process">
                  <n-space item-style="display: flex;">
                    <n-checkbox v-for="lib in availableLibraries" :key="lib.Id" :value="lib.Id" :label="lib.Name" />
                  </n-space>
                </n-checkbox-group>
                <n-text depth="3" v-if="!loadingLibraries && availableLibraries.length === 0 && (configModel.emby_server_url && configModel.emby_api_key)">
                  未找到媒体库。请检查 Emby URL 和 API Key。
                </n-text>
                <div v-if="libraryError" style="color: red; margin-top: 5px;">{{ libraryError }}</div>
              </n-spin>
            </n-form-item-grid-item>

            <!-- ★★★ 分割线: 反向代理 ★★★ -->
            <n-divider title-placement="left" style="margin-top: 20px; margin-bottom: 20px;">
              反向代理
            </n-divider>

            <n-form-item-grid-item label="启用反向代理" path="proxy_enabled">
              <n-switch v-model:value="configModel.proxy_enabled" />
              <template #feedback>
                <n-text depth="3" style="font-size:0.8em;">
                  开启后，自动将自建合集虚拟成媒体库，用反代的端口访问。
                </n-text>
              </template>
            </n-form-item-grid-item>

            <n-form-item-grid-item label="合并原生媒体库" path="proxy_merge_native_libraries">
              <n-switch 
                v-model:value="configModel.proxy_merge_native_libraries"
                :disabled="!configModel.proxy_enabled"
              />
              <template #feedback>
                <n-text depth="3" style="font-size:0.8em;">
                  开启后，将在虚拟库列表合并显示您在 Emby 中配置的原生媒体库（如音乐、电影等）。
                </n-text>
              </template>
            </n-form-item-grid-item>

            <!-- ★★★ FIX: 修复后的“合并显示媒体库”选择 (新功能) ★★★ -->
            <n-form-item-grid-item v-if="configModel.proxy_enabled && configModel.proxy_merge_native_libraries" label="选择合并显示的原生媒体库" path="proxy_native_view_selection">
              <n-spin :show="loadingNativeLibraries">
                <n-checkbox-group v-model:value="configModel.proxy_native_view_selection">
                  <n-space item-style="display: flex;">
                    <n-checkbox 
                      v-for="lib in nativeAvailableLibraries" 
                      :key="lib.Id" 
                      :value="lib.Id" 
                      :label="lib.Name"  
                    />
                  </n-space>
                </n-checkbox-group>
                <n-text depth="3" v-if="!loadingNativeLibraries && nativeAvailableLibraries.length === 0 && (configModel.emby_server_url && configModel.emby_api_key && configModel.emby_user_id)">
                  未找到原生媒体库。请检查 Emby URL、API Key 和 用户ID。
                </n-text>
                <div v-if="nativeLibraryError" style="color: red; margin-top: 5px;">{{ nativeLibraryError }}</div>
              </n-spin>
            </n-form-item-grid-item>

            <n-form-item-grid-item label="原生媒体库显示位置" path="proxy_native_view_order">
              <n-radio-group 
                v-model:value="configModel.proxy_native_view_order"
                :disabled="!configModel.proxy_enabled || !configModel.proxy_merge_native_libraries"
              >
                <n-radio value="before">显示在虚拟库前面</n-radio>
                <n-radio value="after">显示在虚拟库后面</n-radio>
              </n-radio-group>
              <template #feedback>
                <n-text depth="3" style="font-size:0.8em;">
                  选择原生媒体库与虚拟媒体库的合并显示顺序。
                </n-text>
              </template>
            </n-form-item-grid-item>
            <n-form-item-grid-item label="代理监听端口" path="proxy_port">
              <n-input-number 
                v-model:value="configModel.proxy_port" 
                :min="1025" 
                :max="65535"
                :disabled="!configModel.proxy_enabled"
              />
            </n-form-item-grid-item>
          </n-card>  
          <!-- 卡片: AI 翻译设置 -->
          <n-card :bordered="false" class="dashboard-card">
            <template #header>
              <span class="card-title">AI翻译</span>
            </template>
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
          <n-card :bordered="false" class="dashboard-card">
            <template #header>
              <span class="card-title">MoviePilot订阅</span>
            </template>
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
  <!-- 导出选项模态框 -->
  <n-modal v-model:show="exportModalVisible" preset="dialog" title="选择要导出的数据表">
    <n-space justify="end" style="margin-bottom: 10px;">
      <n-button text type="primary" @click="selectAllForExport">全选</n-button>
      <n-button text type="primary" @click="deselectAllForExport">全不选</n-button>
    </n-space>
    <n-checkbox-group v-model:value="tablesToExport" vertical>
      <n-grid :y-gap="8" :cols="2">
        <n-gi v-for="table in allDbTables" :key="table">
          <n-checkbox :value="table">
            {{ tableInfo[table]?.cn || table }}
            <span v-if="tableInfo[table]?.isSharable" class="sharable-label"> [可共享数据]</span>
          </n-checkbox>
        </n-gi>
      </n-grid>
    </n-checkbox-group>
    <template #action>
      <n-button @click="exportModalVisible = false">取消</n-button>
      <n-button type="primary" @click="handleExport" :disabled="tablesToExport.length === 0">确认导出</n-button>
    </template>
  </n-modal>

  <!-- 导入选项模态框 -->
  <n-modal v-model:show="importModalVisible" preset="dialog" title="确认导入选项">
    <n-space vertical>
      <div><p><strong>文件名:</strong> {{ fileToImport?.name }}</p></div>
      <n-form-item label="导入模式" required>
        <n-radio-group v-model:value="importOptions.mode">
          <n-space>
            <n-radio value="merge"><strong>共享合并</strong> 导入别人共享的备份，添加新数据，更新旧数据。</n-radio>
            <n-radio value="overwrite"><strong class="warning-text">本地恢复</strong> (危险!): 仅能导入自己导出的备份！！！</n-radio>
          </n-space>
        </n-radio-group>
      </n-form-item>
      
      <!-- 修正后的复选框部分 -->
      <div>
        <n-text strong>要导入的表 (从文件中自动读取)</n-text>
        <n-space style="margin-left: 20px; display: inline-flex; vertical-align: middle;">
          <n-button size="tiny" text type="primary" @click="selectAllForImport">全选</n-button>
          <n-button size="tiny" text type="primary" @click="deselectAllForImport">全不选</n-button>
        </n-space>
      </div>
      <n-checkbox-group v-model:value="importOptions.tables" vertical style="margin-top: 8px;">
        <n-grid :y-gap="8" :cols="2">
          <n-gi v-for="table in tablesInBackupFile" :key="table">
            <n-checkbox :value="table">
              {{ tableInfo[table]?.cn || table }}
              <span v-if="tableInfo[table]?.isSharable" class="sharable-label"> [可共享数据]</span>
            </n-checkbox>
          </n-gi>
        </n-grid>
      </n-checkbox-group>

    </n-space>
    <template #action>
      <n-button @click="cancelImport">取消</n-button>
      <n-button type="primary" @click="confirmImport" :disabled="importOptions.tables.length === 0">开始导入</n-button>
    </template>
  </n-modal>
</template>

<script setup>
import { ref, watch, computed, onMounted, onUnmounted } from 'vue'; 
import draggable from 'vuedraggable';
import { 
  NCard, NForm, NFormItem, NInputNumber, NSwitch, NButton, NGrid, NGi, 
  NSpin, NAlert, NInput, NSelect, NSpace, useMessage, useDialog,
  NFormItemGridItem, NCheckboxGroup, NCheckbox, NText, NRadioGroup, NRadio,
  NTag, NIcon, NUpload, NModal, NDivider, NInputGroup
} from 'naive-ui';
import { 
  MoveOutline as DragHandleIcon,
  DownloadOutline as ExportIcon, 
  CloudUploadOutline as ImportIcon
} from '@vicons/ionicons5';
import { useConfig } from '../../composables/useConfig.js';
import axios from 'axios';

const tableInfo = {
  'person_identity_map': { cn: '演员身份映射表', isSharable: true },
  'ActorMetadata': { cn: '演员元数据', isSharable: true },
  'translation_cache': { cn: '翻译缓存', isSharable: true },
  'watchlist': { cn: '追剧列表', isSharable: false },
  'actor_subscriptions': { cn: '演员订阅配置', isSharable: false },
  'tracked_actor_media': { cn: '已追踪的演员作品', isSharable: false },
  'collections_info': { cn: '电影合集信息', isSharable: false },
  'processed_log': { cn: '已处理日志', isSharable: false },
  'failed_log': { cn: '待复核日志', isSharable: false },
  'users': { cn: '用户账户', isSharable: false },
  'custom_collections': { cn: '自建合集', isSharable: false },
  'media_metadata': { cn: '媒体元数据', isSharable: false },
};

const formRef = ref(null);
const formRules = {
    trigger: ['input', 'blur']
};
const { configModel, loadingConfig, savingConfig, configError, handleSaveConfig } = useConfig();
const message = useMessage();
const dialog = useDialog();

// --- Emby 相关的 Refs ---
const availableLibraries = ref([]);
const loadingLibraries = ref(false);
const libraryError = ref(null);
const componentIsMounted = ref(false);
// --- 反代相关
const nativeAvailableLibraries = ref([]);
const loadingNativeLibraries = ref(false);
const nativeLibraryError = ref(null);

let unwatchGlobal = null;
let unwatchEmbyConfig = null;

// --- 代理测试 ---
const isTestingProxy = ref(false);

// --- Emby 用户ID 校验逻辑 ---
const embyUserIdRegex = /^[a-f0-9]{32}$/i;
const isInvalidUserId = computed(() => {
  if (!configModel.value || !configModel.value.emby_user_id) return false;
  return configModel.value.emby_user_id.trim() !== '' && !embyUserIdRegex.test(configModel.value.emby_user_id);
});
const embyUserIdRule = {
  trigger: ['input', 'blur'],
  validator(rule, value) {
    if (value && !embyUserIdRegex.test(value)) {
      return new Error('ID格式不正确，应为32位。');
    }
    return true;
  }
};

// ★★★ 新增：测试代理连接的方法 ★★★
const testProxy = async () => {
  if (!configModel.value.network_http_proxy_url) {
    message.warning('请先填写 HTTP 代理地址再进行测试。');
    return;
  }

  isTestingProxy.value = true;
  try {
    const response = await axios.post('/api/proxy/test', {
      url: configModel.value.network_http_proxy_url
    });

    if (response.data.success) {
      message.success(response.data.message);
    } else {
      message.error(`测试失败: ${response.data.message}`);
    }
  } catch (error) {
    const errorMsg = error.response?.data?.message || error.message;
    message.error(`测试请求失败: ${errorMsg}`);
  } finally {
    isTestingProxy.value = false;
  }
};

// --- 反代 合并媒体库 ---
const fetchNativeViewsSimple = async () => {
  if (!configModel.value?.emby_server_url || !configModel.value?.emby_api_key || !configModel.value?.emby_user_id) {
    nativeAvailableLibraries.value = [];
    return;
  }
  loadingNativeLibraries.value = true;
  nativeLibraryError.value = null;
  try {
    const userId = configModel.value.emby_user_id;
    const response = await axios.get(`/api/emby/user/${userId}/views`, {
      headers: {
        'X-Emby-Token': configModel.value.emby_api_key,
      },
    });
    const items = response.data?.Items || [];
    nativeAvailableLibraries.value = items.map(i => ({
      Id: i.Id,
      Name: i.Name,
      CollectionType: i.CollectionType,
    }));
    if (nativeAvailableLibraries.value.length === 0) nativeLibraryError.value = "未找到原生媒体库。";
  } catch (err) {
    nativeAvailableLibraries.value = [];
    nativeLibraryError.value = `获取原生媒体库失败: ${err.response?.data?.error || err.message}`;
  } finally {
    loadingNativeLibraries.value = false;
  }
};

// ★★★ 新增代码：添加这个 watch 监听 ★★★
watch(
  () => configModel.value?.refresh_emby_after_update,
  (isRefreshEnabled) => {
    // 确保 configModel 已经加载
    if (configModel.value) {
      // 如果“刷新”开关被关闭了
      if (!isRefreshEnabled) {
        // 自动将“锁定”开关也关闭
        configModel.value.auto_lock_cast_after_update = false;
      }
    }
  }
);

watch(
  () => [
    configModel.value?.proxy_enabled,
    configModel.value?.proxy_merge_native_libraries,
    configModel.value?.emby_server_url,
    configModel.value?.emby_api_key,
    configModel.value?.emby_user_id,
  ],
  ([proxyEnabled, mergeNative, url, apiKey, userId]) => {
    if (proxyEnabled && mergeNative && url && apiKey && userId) {
      fetchNativeViewsSimple();
    } else {
      nativeAvailableLibraries.value = [];
    }
  },
  { immediate: true }
);

// --- AI 服务商逻辑 ---
const aiProviderOptions = ref([
  { label: 'OpenAI (及兼容服务)', value: 'openai' },
  { label: '智谱AI (ZhipuAI)', value: 'zhipuai' },
  { label: 'Google Gemini', value: 'gemini' },
]);

// --- 数据管理逻辑 ---
const isExporting = ref(false);
const exportModalVisible = ref(false);
const allDbTables = ref([]);
const tablesToExport = ref([]);
const isImporting = ref(false);
const importModalVisible = ref(false);
const fileToImport = ref(null);
const tablesInBackupFile = ref([]);
const importOptions = ref({
  mode: 'merge',
  tables: [],
});

watch(() => importOptions.value.mode, (newMode) => {
  if (importModalVisible.value) {
    if (newMode === 'merge') {
      importOptions.value.tables = tablesInBackupFile.value.filter(t => tableInfo[t]?.isSharable);
    } else {
      importOptions.value.tables = [...tablesInBackupFile.value];
    }
  }
});

const save = async () => {
  try {
    await formRef.value?.validate();
    const success = await handleSaveConfig();
    if (success) {
      message.success('所有设置已成功保存！');
    } else {
      message.error(configError.value || '配置保存失败，请检查后端日志。');
    }
  } catch (errors) {
    console.log('表单验证失败:', errors);
    message.error('请检查表单中的必填项或错误项！');
  }
};

const fetchEmbyLibrariesInternal = async () => {
  if (!configModel.value.emby_server_url || !configModel.value.emby_api_key) {
    availableLibraries.value = [];
    return;
  }
  if (loadingLibraries.value) return;
  loadingLibraries.value = true;
  libraryError.value = null;
  try {
    const response = await axios.get(`/api/emby_libraries`);
    availableLibraries.value = response.data || [];
    if (availableLibraries.value.length === 0) libraryError.value = "获取到的媒体库列表为空。";
  } catch (err) {
    availableLibraries.value = [];
    libraryError.value = `获取 Emby 媒体库失败: ${err.response?.data?.error || err.message}`;
  } finally {
    loadingLibraries.value = false;
  }
};

const showExportModal = async () => {
  try {
    const response = await axios.get('/api/database/tables');
    allDbTables.value = response.data;
    tablesToExport.value = response.data.filter(t => tableInfo[t]?.isSharable);
    exportModalVisible.value = true;
  } catch (error) {
    message.error('无法获取数据库表列表，请检查后端日志。');
  }
};

const handleExport = async () => {
  isExporting.value = true;
  exportModalVisible.value = false;
  try {
    const response = await axios.post('/api/database/export', {
      tables: tablesToExport.value
    }, {
      responseType: 'blob',
    });

    const contentDisposition = response.headers['content-disposition'];
    let filename = 'database_backup.json';
    if (contentDisposition) {
      const match = contentDisposition.match(/filename="?(.+?)"?$/);
      if (match?.[1]) filename = match[1];
    }

    const blobUrl = window.URL.createObjectURL(new Blob([response.data]));
    const link = document.createElement('a');
    link.href = blobUrl;
    link.setAttribute('download', filename);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(blobUrl);

    message.success('数据已开始导出下载！');
  } catch (err) {
    message.error('导出数据失败，请查看日志。');
  } finally {
    isExporting.value = false;
  }
};

const selectAllForExport = () => tablesToExport.value = [...allDbTables.value];
const deselectAllForExport = () => tablesToExport.value = [];

const handleCustomImportRequest = ({ file }) => {
  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const content = JSON.parse(e.target.result);
      if (!content.data || typeof content.data !== 'object') {
        message.error('备份文件格式不正确：缺少 "data" 对象。');
        return;
      }
      tablesInBackupFile.value = Object.keys(content.data);
      if (tablesInBackupFile.value.length === 0) {
        message.error('备份文件格式不正确： "data" 对象为空。');
        return;
      }
      
      if (importOptions.value.mode === 'merge') {
        importOptions.value.tables = tablesInBackupFile.value.filter(t => tableInfo[t]?.isSharable);
      } else {
        importOptions.value.tables = [...tablesInBackupFile.value];
      }
      
      fileToImport.value = file.file;
      importModalVisible.value = true;
    } catch (err) {
      message.error('无法解析JSON文件，请确保文件格式正确。');
    }
  };
  reader.readAsText(file.file);
};

const cancelImport = () => {
  importModalVisible.value = false;
  fileToImport.value = null;
};

const confirmImport = () => {
  importModalVisible.value = false; 
  startImportProcess();   
};
const startImportProcess = (force = false) => {
  isImporting.value = true;
  message.loading('正在上传并处理文件...', { duration: 0 });

  const formData = new FormData();
  formData.append('file', fileToImport.value);
  formData.append('mode', importOptions.value.mode);
  formData.append('tables', importOptions.value.tables.join(','));
  if (force) {
    formData.append('force_overwrite', 'true');
  }

  axios.post('/api/database/import', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  })
  .then(response => {
    isImporting.value = false;
    message.destroyAll();
    message.success(response.data?.message || '导入任务已提交！');
  })
  .catch(error => {
    isImporting.value = false;
    message.destroyAll();
    
    const errorData = error.response?.data;
    
    if (error.response?.status === 409 && errorData?.confirm_required) {
      dialog.warning({
        title: '高危操作确认',
        content: errorData.error, 
        positiveText: '我明白风险，继续覆盖',
        negativeText: '取消',
        positiveButtonProps: { type: 'error' },
        onPositiveClick: () => {
          startImportProcess(true);
        },
      });
    } else {
      message.error(errorData?.error || '导入失败，未知错误。');
    }
  });
};
const selectAllForImport = () => importOptions.value.tables = [...tablesInBackupFile.value];
const deselectAllForImport = () => importOptions.value.tables = [];


// --- 生命周期钩子 ---
onMounted(() => {
  componentIsMounted.value = true;

  unwatchGlobal = watch(loadingConfig, (isLoading) => {
    if (!isLoading && componentIsMounted.value) {
      if (configModel.value && configModel.value.emby_server_url && configModel.value.emby_api_key) {
        fetchEmbyLibrariesInternal();
      }
      if (unwatchGlobal) {
        unwatchGlobal();
      }
    }
  }, { immediate: true });

  unwatchEmbyConfig = watch(
    () => [configModel.value?.emby_server_url, configModel.value?.emby_api_key],
    (newValues, oldValues) => {
      if (componentIsMounted.value && oldValues) {
        if (newValues[0] !== oldValues[0] || newValues[1] !== oldValues[1]) {
          fetchEmbyLibrariesInternal();
        }
      }
    }
  );
});

onUnmounted(() => {
  componentIsMounted.value = false;
  if (unwatchGlobal) unwatchGlobal();
  if (unwatchEmbyConfig) unwatchEmbyConfig();
});
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

/* ★★★ 新增的样式 ★★★ */
.description-text {
  font-size: 0.85em;
  color: var(--n-text-color-3);
  margin: 0;
  line-height: 1.6;
}
.warning-text {
  color: var(--n-warning-color);
  font-weight: bold;
}
.sharable-label {
  color: var(--n-info-color-suppl);
  font-size: 0.9em;
  margin-left: 4px;
  font-weight: normal;
}
.glass-section {
  background-color: rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(10px);
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.2);
}
</style>
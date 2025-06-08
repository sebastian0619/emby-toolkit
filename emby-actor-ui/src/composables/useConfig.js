// src/composables/useConfig.js

import { ref } from 'vue';
import axios from 'axios';

// ✨✨✨ 1. 定义一个包含所有字段的、完整的配置模型结构 ✨✨✨
const createDefaultConfig = () => ({
  // Emby
  emby_server_url: '',
  emby_api_key: '',
  emby_user_id: '',
  refresh_emby_after_update: true,
  libraries_to_process: [],
  // TMDB
  tmdb_api_key: '',
  // Douban
  api_douban_default_cooldown_seconds: 1.0,
  // Translation
  translator_engines_order: ['bing', 'google'],
  // Data Source
  data_source_mode: 'local_then_online',
  local_data_path: '',
  // General
  delay_between_items_sec: 0.5,
  min_score_for_review: 6.0,
  process_episodes: true,
  // Network
  user_agent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
  accept_language: 'zh-CN,zh;q=0.9,en;q=0.8',
  // AI Translation
  ai_translation_enabled: false,
  ai_provider: 'openai',
  ai_api_key: '',
  ai_model_name: 'gpt-3.5-turbo',
  ai_base_url: '',
  ai_translation_prompt: `你是一个专业的影视剧翻译专家。你的任务是精确地翻译演员名或角色名。
规则：
1.  只返回翻译后的文本，不要包含任何额外的解释、标签或标点符号，例如不要说“翻译结果是：”。
2.  如果输入的内容已经是中文，或者是不需要翻译的专有名词（如人名拼音），请直接返回原文。
3.  力求翻译结果“信、达、雅”，符合中文影视圈的常用译法。`
});

// 将 configModel 的定义移到 useConfig 外部，使其成为真正的单例
const configModel = ref(createDefaultConfig());
const loadingConfig = ref(true);
const savingConfig = ref(false);
const configError = ref(null);

export function useConfig() {

  const fetchConfigData = async () => {
    loadingConfig.value = true;
    configError.value = null;
    try {
      const response = await axios.get('/api/config');
      // ✨✨✨ 2. 使用 Object.assign 来合并数据，而不是直接替换 ✨✨✨
      // 这会保留初始结构，只用后端返回的值覆盖对应的字段
      Object.assign(configModel.value, response.data);
    } catch (err) {
      console.error('useConfig: 获取配置失败:', err);
      configError.value = err.response?.data?.error || err.message;
    } finally {
      loadingConfig.value = false;
    }
  };

  const handleSaveConfig = async () => {
    savingConfig.value = true;
    configError.value = null;
    try {
      await axios.post('/api/config', configModel.value);
      // 保存成功后，可以再次获取一次，以确保同步
      // await fetchConfigData(); 
      return true;
    } catch (err) {
      console.error('useConfig: 保存配置失败:', err);
      configError.value = err.response?.data?.error || err.message;
      return false;
    } finally {
      savingConfig.value = false;
    }
  };

  // 在 useConfig 第一次被调用时，如果数据还没加载，就去加载一次
  // 这样可以确保任何页面使用它时，数据都是最新的
  if (loadingConfig.value && configModel.value.emby_server_url === '') {
     // 简单的防止重复请求的逻辑
     // fetchConfigData();
  }


  return {
    configModel,
    loadingConfig,
    savingConfig,
    configError,
    fetchConfigData,
    handleSaveConfig,
  };
}
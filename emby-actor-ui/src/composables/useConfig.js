// src/composables/useConfig.js
import { ref, watch } from 'vue';
import axios from 'axios';

const configModel = ref({
  emby_server_url: '',
  emby_api_key: '',
  emby_user_id: '',
  refresh_emby_after_update: true,
  libraries_to_process: [],
  tmdb_api_key: '',
  translator_engines_order_str: '',
  translator_engines_order: [],
  local_data_path: '',
  data_source_mode: 'local_then_online',
  delay_between_items_sec: 0.5,
  api_douban_default_cooldown_seconds: 1.0,
  min_score_for_review: 6.0,
  schedule_enabled: false,
  schedule_cron: '0 3 * * *',
  schedule_force_reprocess: false,
  schedule_sync_map_enabled: false,
  schedule_sync_map_cron: '0 1 * * *',
});

const loadingConfig = ref(true);
const configError = ref(null); // 用于存储错误信息，供组件读取
const savingConfig = ref(false);
let initialConfigLoaded = false;

export function useConfig() {
    watch(() => configModel.value.translator_engines_order_str, (newStr) => {
        if (typeof newStr === 'string') {
            configModel.value.translator_engines_order = newStr.split(',').map(s => s.trim()).filter(s => s);
        }
    });
    watch(() => configModel.value.translator_engines_order, (newArr) => {
        if (Array.isArray(newArr)) {
            configModel.value.translator_engines_order_str = newArr.join(',');
        }
    }, { immediate: true });

    const fetchConfigData = async () => {
        if (initialConfigLoaded && !loadingConfig.value) {
            return;
        }
        loadingConfig.value = true;
        configError.value = null; // 清除旧错误
        try {
            const response = await axios.get(`/api/config`);
            const backendConfig = response.data;
            console.log('[useConfig] fetchConfigData: Received backendConfig:', JSON.stringify(backendConfig)); 
            console.log('[useConfig] fetchConfigData: backendConfig.data_source_mode:', backendConfig.data_source_mode);
            Object.keys(configModel.value).forEach(key => {
                if (backendConfig.hasOwnProperty(key)) {
                    if (key === 'translator_engines_order' || key === 'libraries_to_process') {
                        configModel.value[key] = Array.isArray(backendConfig[key]) ? backendConfig[key] : [];
                    } else if (key === 'translator_engines_order_str') {
                         if (typeof backendConfig[key] === 'string') {
                            configModel.value[key] = backendConfig[key];
                        }
                    } else if (typeof configModel.value[key] === 'number' && typeof backendConfig[key] !== 'number') {
                        const numVal = parseFloat(backendConfig[key]);
                        configModel.value[key] = isNaN(numVal) ? (configModel.value[key] || 0) : numVal;
                    } else if (typeof configModel.value[key] === 'boolean' && typeof backendConfig[key] !== 'boolean') {
                        configModel.value[key] = String(backendConfig[key]).toLowerCase() === 'true';
                    }
                    else {
                        configModel.value[key] = backendConfig[key];
                    }
                }
            });

            if (!configModel.value.translator_engines_order_str && configModel.value.translator_engines_order.length > 0) {
                configModel.value.translator_engines_order_str = configModel.value.translator_engines_order.join(',');
            } else if (configModel.value.translator_engines_order_str && configModel.value.translator_engines_order.length === 0) {
                 configModel.value.translator_engines_order = configModel.value.translator_engines_order_str.split(',')
                    .map(s => s.trim())
                    .filter(s => s);
            }
            initialConfigLoaded = true;
            console.log('[useConfig] fetchConfigData: AFTER assigning, configModel.data_source_mode:', configModel.value.data_source_mode);
        } catch (err) {
            console.error("useConfig: 获取配置失败:", err);
            configError.value = "加载配置失败！请检查后端服务和网络连接。";
        } finally {
            loadingConfig.value = false;
        }
    };

    const handleSaveConfig = async () => {
        savingConfig.value = true;
        configError.value = null; // 清除旧错误
        try {
            const payload = { ...configModel.value };
            if (typeof payload.translator_engines_order_str === 'string') {
                 payload.translator_engines_order = payload.translator_engines_order_str.split(',')
                    .map(s => s.trim())
                    .filter(s => s);
            }
            console.log('[useConfig] handleSaveConfig: Payload to be sent:', JSON.stringify(payload)); 
            console.log('[useConfig] handleSaveConfig: data_source_mode in payload:', payload.data_source_mode); 
            await axios.post(`/api/config`, payload);
            console.log('配置已成功保存！后端将重新加载。');
            initialConfigLoaded = false;
            await fetchConfigData(); // 重新获取配置以确保与后端同步
            return true; // 返回成功状态
        } catch (err) {
            console.error("useConfig: 保存配置失败:", err);
            let errMsg = "保存配置失败。";
            if (err.response && err.response.data && err.response.data.error) {
                errMsg = `保存失败: ${err.response.data.error}`;
            } else if (err.message) {
                errMsg = `保存失败: ${err.message}`;
            }
            configError.value = errMsg; // 设置错误信息
            return false; // 返回失败状态
        } finally {
            savingConfig.value = false;
        }
    };

    return {
        configModel,
        loadingConfig,
        configError, // 暴露 configError 供组件使用
        savingConfig,
        fetchConfigData,
        handleSaveConfig // 暴露原始的保存函数
    };
}
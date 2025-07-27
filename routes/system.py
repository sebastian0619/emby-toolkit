# routes/system.py

from flask import Blueprint, jsonify, request
import logging
import re

# 导入底层模块
import task_manager
from logger_setup import frontend_log_queue
import config_manager
# 导入共享模块
import extensions
from extensions import login_required, processor_ready_required
import tasks
# 1. 创建蓝图
system_bp = Blueprint('system', __name__, url_prefix='/api')
logger = logging.getLogger(__name__)

# 2. 定义路由

# --- 任务状态与控制 ---
@system_bp.route('/status', methods=['GET'])
def api_get_task_status():
    status_data = task_manager.get_task_status()
    status_data['logs'] = list(frontend_log_queue)
    return jsonify(status_data)

@system_bp.route('/trigger_stop_task', methods=['POST'])
def api_handle_trigger_stop_task():
    logger.debug("API (Blueprint): Received request to stop current task.")
    stopped_any = False
    if extensions.media_processor_instance:
        extensions.media_processor_instance.signal_stop()
        stopped_any = True
    if extensions.watchlist_processor_instance:
        extensions.watchlist_processor_instance.signal_stop()
        stopped_any = True
    if extensions.actor_subscription_processor_instance:
        extensions.actor_subscription_processor_instance.signal_stop()
        stopped_any = True

    if stopped_any:
        return jsonify({"message": "已发送停止任务请求。"}), 200
    else:
        return jsonify({"error": "核心处理器未就绪"}), 503

# ✨✨✨ “立即执行”API接口 ✨✨✨
@system_bp.route('/tasks/trigger/<task_identifier>', methods=['POST'])
@extensions.login_required
@extensions.task_lock_required
def api_trigger_task_now(task_identifier: str):
    task_registry = tasks.get_task_registry()
    task_info = task_registry.get(task_identifier)
    if not task_info:
        return jsonify({"status": "error", "message": f"未知的任务标识符: {task_identifier}"}), 404

    task_function, task_name = task_info
    kwargs = {}
    if task_identifier == 'full-scan':
        data = request.get_json(silent=True) or {}
        kwargs['process_episodes'] = data.get('process_episodes', True)
    
    success = task_manager.submit_task(task_function, task_name, **kwargs)
    
    if success:
        return jsonify({"status": "success", "message": "任务已成功提交到后台队列。", "task_name": task_name}), 202
    else:
        return jsonify({"status": "error", "message": "提交任务失败，已有任务在运行。"}), 409
    
# --- API 端点：获取当前配置 ---
@system_bp.route('/config', methods=['GET'])
def api_get_config():
    try:
        # ★★★ 确保这里正确解包了元组 ★★★
        current_config = config_manager.APP_CONFIG 
        
        if current_config:
            current_config['emby_server_id'] = extensions.EMBY_SERVER_ID
            logger.trace(f"API /api/config (GET): 成功加载并返回配置。")
            return jsonify(current_config)
        else:
            logger.error(f"API /api/config (GET): config_manager.APP_CONFIG 为空或未初始化。")
            return jsonify({"error": "无法加载配置数据"}), 500
    except Exception as e:
        logger.error(f"API /api/config (GET) 获取配置时发生错误: {e}", exc_info=True)
        return jsonify({"error": "获取配置信息时发生服务器内部错误"}), 500


# --- API 端点：保存配置 ---
@system_bp.route('/config', methods=['POST'])
def api_save_config():
    from tasks import save_config_and_reload
    try:
        new_config_data = request.json
        if not new_config_data:
            return jsonify({"error": "请求体中未包含配置数据"}), 400
        
        # ★★★ 核心修改：在这里进行严格校验并“打回去” ★★★
        user_id_to_save = new_config_data.get("emby_user_id", "").strip()

        # 规则1：检查是否为空
        if not user_id_to_save:
            error_message = "Emby User ID 不能为空！这是获取媒体库列表的必需项。"
            logger.warning(f"API /api/config (POST): 拒绝保存，原因: {error_message}")
            return jsonify({"error": error_message}), 400

        # 规则2：检查格式是否正确
        if not re.match(r'^[a-f0-9]{32}$', user_id_to_save, re.I):
            error_message = "Emby User ID 格式不正确！它应该是一串32位的字母和数字。"
            logger.warning(f"API /api/config (POST): 拒绝保存，原因: {error_message} (输入值: '{user_id_to_save}')")
            return jsonify({"error": error_message}), 400
        # ★★★ 校验结束 ★★★

        logger.info(f"API /api/config (POST): 收到新的配置数据，准备保存...")
        
        # 校验通过后，才调用保存函数
        save_config_and_reload(new_config_data)  
        
        logger.debug("API /api/config (POST): 配置已成功传递给 save_config 函数。")
        return jsonify({"message": "配置已成功保存并已触发重新加载。"})
        
    except Exception as e:
        logger.error(f"API /api/config (POST) 保存配置时发生错误: {e}", exc_info=True)
        return jsonify({"error": f"保存配置时发生服务器内部错误: {str(e)}"}), 500


# routes/system.py

from flask import Blueprint, jsonify, request, Response, stream_with_context
import logging
import json
import re
import os
import threading
import docker
# 导入底层模块
import task_manager
from logger_setup import frontend_log_queue
import config_manager
import config_manager
# 导入共享模块
import extensions
from extensions import login_required, task_lock_required
import tasks
import constants
import github_handler
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
@login_required
@task_lock_required
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
    from web_app import save_config_and_reload
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
    
# +++ 关于页面的信息接口 +++
@system_bp.route('/system/about_info', methods=['GET'])
def get_about_info():
    """
    【V2 - 支持认证版】获取关于页面的所有信息，包括当前版本和 GitHub releases。
    会从配置中读取 GitHub Token 用于认证，以提高 API 速率限制。
    """
    try:
        # ★★★ 1. 从全局配置中获取 GitHub Token ★★★
        github_token = config_manager.APP_CONFIG.get(constants.CONFIG_OPTION_GITHUB_TOKEN)

        # ★★★ 2. 将 Token 传递给 get_github_releases 函数 ★★★
        releases = github_handler.get_github_releases(
            owner=constants.GITHUB_REPO_OWNER,
            repo=constants.GITHUB_REPO_NAME,
            token=github_token  # <--- 将令牌作为参数传入
        )

        if releases is None:
            # 即使获取失败，也返回一个正常的结构，只是 releases 列表为空
            releases = []
            logger.warning("API /system/about_info: 从 GitHub 获取 releases 失败，将返回空列表。")

        response_data = {
            "current_version": constants.APP_VERSION,
            "releases": releases
        }
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"API /system/about_info 发生错误: {e}", exc_info=True)
        return jsonify({"error": "获取版本信息时发生服务器内部错误"}), 500

# --- 一键更新 ---
@system_bp.route('/system/update/stream', methods=['GET'])
@login_required
def stream_update_progress():
    """
    【V7 - 最终流式版】通过 Server-Sent Events (SSE) 实时流式传输更新进度。
    """
    def generate_progress():
        container_name = os.environ.get('CONTAINER_NAME', 'emby-toolkit')
        watchtower_container_name = "watchtower_eap_updater"
        
        def send_event(data):
            """辅助函数，用于格式化并发送 SSE 事件。"""
            yield f"data: {json.dumps(data)}\n\n"

        try:
            client = docker.from_env()
            container = client.containers.get(container_name)
            
            if not container.image.tags:
                yield from send_event({"status": "错误：无法确定镜像名称。", "progress": -1, "event": "ERROR"})
                return
            
            image_name_tag = container.image.tags[0]

            # 检查是否有新版本
            yield from send_event({"status": f"正在拉取最新镜像: {image_name_tag}...", "progress": 0})
            
            # 使用低级 API 以获取流式输出
            stream = client.api.pull(image_name_tag, stream=True, decode=True)
            
            layer_progress = {}
            image_pulled = False
            for line in stream:
                if 'status' in line:
                    status = line['status']
                    layer_id = line.get('id')
                    
                    if status == 'Downloading' and 'progressDetail' in line and layer_id:
                        details = line['progressDetail']
                        if details.get('total'):
                            layer_progress[layer_id] = details
                            
                            total_size = sum(l.get('total', 0) for l in layer_progress.values())
                            current_size = sum(l.get('current', 0) for l in layer_progress.values())
                            
                            if total_size > 0:
                                progress = int((current_size / total_size) * 100)
                                yield from send_event({"status": f"正在下载... ({layer_id[:12]})", "progress": progress})
                    
                    elif status == 'Download complete' or status == 'Pull complete':
                        yield from send_event({"status": status, "progress": 100})
                    
                    elif 'Status:' in status: # 这是一个摘要状态
                        image_pulled = True
            
            if not image_pulled:
                 yield from send_event({"status": "当前已是最新版本。", "progress": 100})
                 yield from send_event({"event": "DONE", "message": "无需更新。"})
                 return

            # 拉取完成，触发 Watchtower
            yield from send_event({"status": "新镜像拉取完成，正在触发更新...", "progress": 100})
            try:
                watchtower_container = client.containers.get(watchtower_container_name)
                watchtower_container.restart()
                yield from send_event({"status": "更新应用成功！请在约1分钟后手动刷新页面。", "progress": 100})
            except Exception as e_restart:
                yield from send_event({"status": f"错误：重启 Watchtower 失败: {e_restart}", "progress": -1, "event": "ERROR"})

            # 发送结束信号
            yield from send_event({"event": "DONE", "message": "更新流程已触发。"})

        except Exception as e:
            error_message = f"更新过程中发生错误: {str(e)}"
            logger.error(f"[Update Stream]: {error_message}", exc_info=True)
            yield from send_event({"status": error_message, "progress": -1, "event": "ERROR"})

    # 返回一个流式响应
    return Response(stream_with_context(generate_progress()), mimetype='text/event-stream')
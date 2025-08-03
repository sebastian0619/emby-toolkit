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
import db_handler
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
@task_lock_required
def stream_update_progress():
    """
    【V10 - 最终可行自更新版】
    通过启动一个临时的“更新器容器”来执行更新操作，解决了“进程自杀”的悖论。
    """
    def generate_progress():
        def send_event(data):
            yield f"data: {json.dumps(data)}\n\n"

        client = None
        try:
            client = docker.from_env()
            container_name = config_manager.APP_CONFIG.get('container_name', 'emby-toolkit')
            image_name_tag = config_manager.APP_CONFIG.get('docker_image_name', 'hbq0405/emby-toolkit:latest')


            yield from send_event({"status": f"正在检查并拉取最新镜像: {image_name_tag}...", "layers": {}})
            
            # 使用低级 API 获取流式输出
            stream = client.api.pull(image_name_tag, stream=True, decode=True)
            
            # ★★★ 2. 重新设计状态跟踪变量 ★★★
            layers_status = {}
            is_new_image_pulled = False
            all_layer_ids = set()

            for line in stream:
                layer_id = line.get('id')
                status = line.get('status')

                # ★ 1. 改进对全局状态行的处理，并用它来识别所有层
                if not layer_id and status:
                    # 当Docker说 "Pulling fs layer" 时，它正在注册一个新的层
                    if 'Pulling fs layer' in status:
                        # 这时事件流里还没有ID，我们暂时无法获取，但可以预见会有新层
                        pass
                    yield from send_event({"status": status, "layers": layers_status})
                    continue

                # ★ 2. 过滤掉无效的层ID，比如 'latest'
                if not layer_id or len(layer_id) < 10: # 真实的层ID通常是一长串字符
                    continue
                
                # 记录所有出现过的真实层ID
                all_layer_ids.add(layer_id)

                # 初始化或更新层的状态
                if layer_id not in layers_status:
                    layers_status[layer_id] = {"status": "", "progress": 0, "detail": ""}
                
                layers_status[layer_id]['status'] = status

                # 处理进度详情
                if 'progressDetail' in line:
                    details = line['progressDetail']
                    current = details.get('current', 0)
                    total = details.get('total', 0)
                    layers_status[layer_id]['current_bytes'] = current
                    layers_status[layer_id]['total_bytes'] = total
                    if total > 0:
                        progress_percent = int((current / total) * 100)
                        layers_status[layer_id]['progress'] = progress_percent
                        # 将字节转换为易读的 MB
                        current_mb = round(current / (1024 * 1024), 2)
                        total_mb = round(total / (1024 * 1024), 2)
                        layers_status[layer_id]['detail'] = f"{current_mb}MB / {total_mb}MB"
                
                # 处理非下载状态
                if any(s in status for s in ["Pull complete", "Already exists", "Download complete"]):
                    layers_status[layer_id]['progress'] = 100
                    layers_status[layer_id]['detail'] = "" # 完成后清空详情

                completed_layers = 0
                for lid in all_layer_ids:
                    # 检查这个层是否已经出现在状态字典中，并且状态是完成状态
                    if lid in layers_status and layers_status[lid].get('progress') == 100:
                        completed_layers += 1
                
                total_layers = len(all_layer_ids)
                overall_progress = int((completed_layers / total_layers) * 100) if total_layers > 0 else 0
                
                # ★ 4. 每次循环都发送完整的状态对象
                yield from send_event({
                    "status": "正在拉取...", 
                    "layers": layers_status, 
                    "overall_progress": overall_progress
                })

                last_status_line = line
                # 检查是否有新内容被拉取
                if status == "Pull complete":
                    is_new_image_pulled = True
            
            # 检查最终状态
            final_status_line = line.get('status', '')
            if 'Status: Image is up to date' in final_status_line:
                 yield from send_event({"status": "当前已是最新版本。", "progress": 100})
                 yield from send_event({"event": "DONE", "message": "无需更新。"})
                 return

            # --- 2. ★★★ 核心：召唤并启动“更新器容器” ★★★ ---
            yield from send_event({"status": "准备应用更新...", "progress": 70})

            try:
                # 获取旧容器的完整配置，以便新容器可以重建它
                old_container = client.containers.get(container_name)
                
                # Watchtower 使用的官方工具镜像，非常小巧可靠
                updater_image = "containrrr/watchtower"
                
                # 构建传递给更新器容器的命令
                # --cleanup 会移除旧镜像，--run-once 会让它执行一次就退出
                command = [
                    "--cleanup",
                    "--run-once",
                    container_name # 明确告诉 watchtower 只更新我们自己
                ]

                # 启动更新器容器！
                logger.info(f"正在应用更新 '{container_name}'...")
                client.containers.run(
                    image=updater_image,
                    command=command,
                    remove=True,  # a.k.a. --rm，任务完成后自动删除自己
                    detach=True,  # 在后台运行
                    volumes={'/var/run/docker.sock': {'bind': '/var/run/docker.sock', 'mode': 'rw'}}
                )
                
                yield from send_event({"status": "更新任务已成功交接给临时更新器！本容器将在后台被重启。", "progress": 90})
                yield from send_event({"status": "稍后手动刷新页面以访问新版本。", "progress": 100, "event": "DONE"})

            except docker.errors.NotFound:
                yield from send_event({"status": f"错误：找不到名为 '{container_name}' 的容器来更新。", "event": "ERROR"})
            except Exception as e_updater:
                error_msg = f"错误：启动临时更新器时失败: {e_updater}"
                logger.error(error_msg, exc_info=True)
                yield from send_event({"status": error_msg, "event": "ERROR"})

        except Exception as e:
            error_message = f"更新过程中发生未知错误: {str(e)}"
            logger.error(f"[Update Stream]: {error_message}", exc_info=True)
            yield from send_event({"status": error_message, "event": "ERROR"})

    return Response(stream_with_context(generate_progress()), mimetype='text/event-stream')

# ★★★ 提供电影类型映射的API ★★★
@system_bp.route('/config/genres', methods=['GET'])
@login_required
def api_get_genres_config():
    """
    (V2 - 数据库驱动版)
    从媒体元数据缓存中动态获取所有唯一的电影类型。
    """
    try:
        # ★★★ 核心修复：直接调用新的数据库函数 ★★★
        genres = db_handler.get_unique_genres(config_manager.DB_PATH)
        # API直接返回一个字符串列表，例如 ["动作", "喜剧", "科幻"]
        return jsonify(genres)
    except Exception as e:
        logger.error(f"动态获取电影类型时发生错误: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500
    
# ★★★ 提供电影工作室列表的API ★★★
@system_bp.route('/config/studios', methods=['GET'])
@login_required
def api_get_studios_config():
    """
    从媒体元数据缓存中动态获取所有唯一的工作室。
    """
    try:
        studios = db_handler.get_unique_studios(config_manager.DB_PATH)
        return jsonify(studios)
    except Exception as e:
        logger.error(f"动态获取工作室列表时发生错误: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500
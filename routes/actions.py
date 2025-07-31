# routes/actions.py

from flask import Blueprint, request, jsonify
import logging

# 导入底层和共享模块
import task_manager
import extensions
from extensions import login_required, processor_ready_required, task_lock_required

# 1. 创建蓝图
actions_bp = Blueprint('actions', __name__, url_prefix='/api')
logger = logging.getLogger(__name__)

# 2. 定义路由

# --- 全量扫描接口 ---   
@actions_bp.route('/trigger_full_scan', methods=['POST'])
@processor_ready_required
@task_lock_required
def api_handle_trigger_full_scan():
    # ★★★ 延迟导入两个任务 ★★★
    from tasks import task_process_full_library, task_force_reprocess_full_library 
    from config_manager import APP_CONFIG
    
    # 1. 检查前端是否勾选了“强制重处理”
    force_reprocess = request.form.get('force_reprocess_all') == 'on'
    
    # 2. 根据 `force_reprocess` 的值，决定调用哪个任务和显示什么消息
    if force_reprocess:
        task_to_run = task_force_reprocess_full_library
        action_message = "全量媒体库扫描 (强制重处理)"
        logger.info("API层：接收到强制全量扫描请求，将提交强制任务。")
    else:
        task_to_run = task_process_full_library
        action_message = "全量媒体库扫描 (标准模式)"
        logger.info("API层：接收到标准全量扫描请求，将提交标准任务。")

    # 3. 获取通用参数
    process_episodes = APP_CONFIG.get('process_episodes', True)
    
    # 4. 提交选择好的任务
    success = task_manager.submit_task(task_to_run, action_message, process_episodes)
    
    if success:
        return jsonify({"message": f"{action_message} 任务已提交启动。"}), 202
    else:
        return jsonify({"error": "提交任务失败，已有任务在运行。"}), 409

# --- 同步演员映射表 ---
@actions_bp.route('/trigger_sync_person_map', methods=['POST'])
@login_required
def api_handle_trigger_sync_map():
    from tasks import task_sync_person_map # 延迟导入
    success = task_manager.submit_task(task_sync_person_map, "同步演员映射表")
    if success:
        return jsonify({"message": "'同步演员映射表' 任务已提交启动。"}), 202
    else:
        return jsonify({"error": "提交任务失败，已有任务在运行。"}), 409

# ★★★ 重新处理单个项目 ★★★
@actions_bp.route('/actions/reprocess_item/<item_id>', methods=['POST'])
@login_required
@task_lock_required
def api_reprocess_item(item_id):
    from tasks import task_reprocess_single_item # 延迟导入
    import emby_handler

    item_details = emby_handler.get_emby_item_details(
        item_id,
        extensions.media_processor_instance.emby_url,
        extensions.media_processor_instance.emby_api_key,
        extensions.media_processor_instance.emby_user_id
    )
    item_name_for_ui = item_details.get("Name", f"ItemID: {item_id}") if item_details else f"ItemID: {item_id}"

    success = task_manager.submit_task(
        task_reprocess_single_item,
        f"任务已提交: {item_name_for_ui}",
        item_id,
        item_name_for_ui
    )
    if success:
        return jsonify({"message": f"重新处理项目 '{item_name_for_ui}' 的任务已提交。"}), 202
    else:
        return jsonify({"error": "提交任务失败，已有任务在运行。"}), 409

# ★★★ 重新处理所有待复核项 ★★★
@actions_bp.route('/actions/reprocess_all_review_items', methods=['POST'])
@login_required
@task_lock_required
@processor_ready_required
def api_reprocess_all_review_items():
    from tasks import task_reprocess_all_review_items # 延迟导入
    success = task_manager.submit_task(task_reprocess_all_review_items, "重新处理所有待复核项")
    if success:
        return jsonify({"message": "重新处理所有待复核项的任务已提交。"}), 202
    else:
        return jsonify({"error": "提交任务失败，已有任务在运行。"}), 409

# ★★★ 全量图片同步的 API 接口 ★★★
@actions_bp.route('/actions/trigger_full_image_sync', methods=['POST'])
@login_required
@task_lock_required
@processor_ready_required
def api_trigger_full_image_sync():
    from tasks import task_full_image_sync # 延迟导入
    success = task_manager.submit_task(task_full_image_sync, "全量同步媒体库海报")
    if success:
        return jsonify({"message": "全量海报同步任务已成功提交。"}), 202
    else:
        return jsonify({"error": "提交任务失败，已有任务在运行。"}), 409

# --- 一键重构演员数据端点 ---
@actions_bp.route('/tasks/rebuild-actors', methods=['POST'])
@login_required
@task_lock_required
@processor_ready_required
def trigger_rebuild_actors_task():
    from tasks import run_full_rebuild_task # 延迟导入
    success = task_manager.submit_task(run_full_rebuild_task, "重构演员数据库")
    if success:
        return jsonify({"status": "success", "message": "重构演员数据库任务已成功提交到后台队列。"}), 202
    else:
        return jsonify({"status": "error", "message": "提交任务失败，已有任务在运行。"}), 409
    
# +++ 一键添加所有剧集到追剧列表的 API +++
@actions_bp.route('/actions/add_all_series_to_watchlist', methods=['POST'])
@login_required
@task_lock_required
@processor_ready_required
def api_add_all_series_to_watchlist():
    from tasks import task_add_all_series_to_watchlist # 延迟导入
    
    success = task_manager.submit_task(
        task_add_all_series_to_watchlist, 
        "一键扫描全库剧集"
    )
    
    if success:
        return jsonify({"message": "一键扫描全库剧集的任务已提交。"}), 202
    else:
        return jsonify({"error": "提交任务失败，已有任务在运行。"}), 409
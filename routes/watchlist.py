# routes/watchlist.py

from flask import Blueprint, request, jsonify
import logging

# 导入需要的模块
import db_handler
import config_manager
import task_manager
from extensions import login_required, task_lock_required, watchlist_processor_instance

# 1. 创建追剧列表蓝图
watchlist_bp = Blueprint('watchlist', __name__, url_prefix='/api/watchlist')

logger = logging.getLogger(__name__)

# 2. 使用蓝图定义路由
@watchlist_bp.route('', methods=['GET']) # 注意：这里的路径是空的，因为前缀已经定义
@login_required
def api_get_watchlist():
    logger.debug("API (Blueprint): 收到获取追剧列表的请求。")
    try:
        items = db_handler.get_all_watchlist_items(config_manager.DB_PATH)
        return jsonify(items)
    except Exception as e:
        logger.error(f"获取追剧列表时发生错误: {e}", exc_info=True)
        return jsonify({"error": "获取追剧列表时发生服务器内部错误"}), 500

@watchlist_bp.route('/add', methods=['POST'])
@login_required
def api_add_to_watchlist():
    # ... (函数逻辑和原来完全一样) ...
    data = request.json
    item_id = data.get('item_id')
    tmdb_id = data.get('tmdb_id')
    item_name = data.get('item_name')
    item_type = data.get('item_type')

    if not all([item_id, tmdb_id, item_name, item_type]):
        return jsonify({"error": "缺少必要的项目信息"}), 400
    
    if item_type != 'Series':
        return jsonify({"error": "只能将'剧集'类型添加到追剧列表"}), 400

    logger.info(f"API (Blueprint): 收到手动添加 '{item_name}' 到追剧列表的请求。")
    
    try:
        db_handler.add_item_to_watchlist(
            db_path=config_manager.DB_PATH,
            item_id=item_id,
            tmdb_id=tmdb_id,
            item_name=item_name,
            item_type=item_type
        )
        return jsonify({"message": f"《{item_name}》已成功添加到追剧列表！"}), 200
    except Exception as e:
        logger.error(f"手动添加项目到追剧列表时发生错误: {e}", exc_info=True)
        return jsonify({"error": "服务器在添加时发生内部错误"}), 500

@watchlist_bp.route('/update_status', methods=['POST'])
@login_required
@task_lock_required
def api_update_watchlist_status():
    # ... (函数逻辑和原来完全一样) ...
    data = request.json
    item_id = data.get('item_id')
    new_status = data.get('new_status')

    if not item_id or new_status not in ['Watching', 'Ended', 'Paused']:
        return jsonify({"error": "请求参数无效"}), 400

    logger.info(f"API (Blueprint): 收到请求，将项目 {item_id} 的追剧状态更新为 '{new_status}'。")
    try:
        success = db_handler.update_watchlist_item_status(
            db_path=config_manager.DB_PATH,
            item_id=item_id,
            new_status=new_status
        )
        if success:
            return jsonify({"message": "状态更新成功"}), 200
        else:
            return jsonify({"error": "未在追剧列表中找到该项目"}), 404
    except Exception as e:
        logger.error(f"更新追剧状态时发生错误: {e}", exc_info=True)
        return jsonify({"error": "服务器在更新状态时发生内部错误"}), 500

@watchlist_bp.route('/remove/<item_id>', methods=['POST'])
@login_required
@task_lock_required
def api_remove_from_watchlist(item_id):
    # ... (函数逻辑和原来完全一样) ...
    logger.info(f"API (Blueprint): 收到请求，将项目 {item_id} 从追剧列表移除。")
    try:
        success = db_handler.remove_item_from_watchlist(
            db_path=config_manager.DB_PATH,
            item_id=item_id
        )
        if success:
            return jsonify({"message": "已从追剧列表移除"}), 200
        else:
            return jsonify({"error": "未在追剧列表中找到该项目"}), 404
    except Exception as e:
        logger.error(f"从追剧列表移除项目时发生未知错误: {e}", exc_info=True)
        return jsonify({"error": "移除项目时发生未知的服务器内部错误"}), 500

@watchlist_bp.route('/refresh/<item_id>', methods=['POST'])
@login_required
@task_lock_required
def api_trigger_single_watchlist_refresh(item_id):
    # ... (函数逻辑和原来完全一样) ...
    from web_app import task_refresh_single_watchlist_item # 延迟导入任务函数
    logger.trace(f"API (Blueprint): 收到对单个追剧项目 {item_id} 的刷新请求。")
    if not watchlist_processor_instance:
        return jsonify({"error": "追剧处理模块未就绪"}), 503

    item_name = db_handler.get_watchlist_item_name(config_manager.DB_PATH, item_id) or "未知剧集"

    task_manager.submit_task(
        task_refresh_single_watchlist_item,
        f"手动刷新: {item_name}",
        item_id
    )
    
    return jsonify({"message": f"《{item_name}》的刷新任务已在后台启动！"}), 202
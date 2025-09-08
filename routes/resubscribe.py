# routes/resubscribe.py (完整版)

from flask import Blueprint, request, jsonify
import logging
import db_handler
import tasks
import task_manager
import moviepilot_handler
import config_manager
from extensions import login_required, task_lock_required

resubscribe_bp = Blueprint('resubscribe', __name__, url_prefix='/api/resubscribe')
logger = logging.getLogger(__name__)

# --- 设置读写 API ---
@resubscribe_bp.route('/settings', methods=['GET'])
@login_required
def get_settings():
    try:
        settings = db_handler.get_resubscribe_settings()
        return jsonify(settings)
    except Exception as e:
        logger.error(f"获取洗版设置API出错: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500

@resubscribe_bp.route('/settings', methods=['POST'])
@login_required
def save_settings():
    try:
        settings_data = request.json
        db_handler.save_resubscribe_settings(settings_data)
        return jsonify({"message": "智能洗版设置已成功保存！"})
    except Exception as e:
        logger.error(f"保存洗版设置API出错: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500

# ★★★ 新增：获取海报墙数据的 API ★★★
@resubscribe_bp.route('/library_status', methods=['GET'])
@login_required
def get_library_status():
    try:
        items = db_handler.get_all_resubscribe_cache()
        return jsonify(items)
    except Exception as e:
        logger.error(f"获取洗版状态缓存API出错: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500

# ★★★ 新增：触发缓存刷新任务的 API ★★★
@resubscribe_bp.route('/refresh_status', methods=['POST'])
@login_required
@task_lock_required
def trigger_refresh_status():
    try:
        task_manager.submit_task(
            tasks.task_update_resubscribe_cache,
            task_name="刷新媒体洗版状态",
            processor_type='media'
        )
        return jsonify({"message": "刷新媒体洗版状态任务已提交！"}), 202
    except Exception as e:
        return jsonify({"error": f"提交任务失败: {e}"}), 500

# ★★★ 新增：触发一键洗版全部的 API ★★★
@resubscribe_bp.route('/resubscribe_all', methods=['POST'])
@login_required
@task_lock_required
def trigger_resubscribe_all():
    try:
        task_manager.submit_task(
            tasks.task_resubscribe_library,
            task_name="全库媒体智能洗版",
            processor_type='media'
        )
        return jsonify({"message": "一键洗版任务已提交！"}), 202
    except Exception as e:
        return jsonify({"error": f"提交任务失败: {e}"}), 500

# ★★★ 单独洗版一个媒体项的 API ★★★
@resubscribe_bp.route('/resubscribe_item', methods=['POST'])
@login_required
def resubscribe_single_item():
    """
    API端点：为单个媒体项提交洗版订阅，并立即将其数据库状态更新为 'subscribed'。
    """
    data = request.json
    item_id = data.get('item_id')
    item_name = data.get('item_name')
    tmdb_id = data.get('tmdb_id')
    item_type = data.get('item_type')

    if not all([item_id, item_name, tmdb_id, item_type]):
        return jsonify({"error": "请求中缺少必要的媒体项参数"}), 400

    try:
        # 步骤 1: 准备提交给 MoviePilot 的纯粹洗版请求
        # 我们需要主配置来获取MoviePilot的凭据
        main_config = config_manager.APP_CONFIG
        payload = {
            "name": item_name,
            "tmdbid": int(tmdb_id),
            "type": "电影" if item_type == "Movie" else "电视剧",
            "best_version": 1
        }
        
        # 步骤 2: 调用 MoviePilot 处理器提交订阅
        success = moviepilot_handler.subscribe_with_custom_payload(payload, main_config)
        
        if success:
            # 步骤 3: 订阅成功后，立即更新数据库中的状态为 'subscribed'
            logger.info(f"API: 洗版订阅成功 for '{item_name}'，正在更新数据库状态...")
            db_handler.update_resubscribe_item_status(item_id, 'subscribed')
            
            return jsonify({"message": f"《{item_name}》的洗版请求已成功提交！"})
        else:
            # 如果订阅失败，不改变数据库状态，并返回错误
            logger.error(f"API: 提交《{item_name}》的洗版订阅请求到 MoviePilot 失败。")
            return jsonify({"error": "提交洗版请求失败，请检查MoviePilot连接或查看后端日志。"}), 500
            
    except Exception as e:
        logger.error(f"API: 处理洗版订阅请求时发生未知错误: {e}", exc_info=True)
        return jsonify({"error": f"处理请求时发生服务器内部错误: {e}"}), 500
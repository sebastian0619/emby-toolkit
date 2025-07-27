# routes/actor_subscriptions.py

from flask import Blueprint, request, jsonify
import logging
import sqlite3

# 导入需要的模块
import db_handler
import config_manager
import tmdb_handler
import task_manager
from extensions import login_required, task_lock_required, processor_ready_required

# 1. 创建演员订阅蓝图
actor_subscriptions_bp = Blueprint('actor_subscriptions', __name__, url_prefix='/api/actor-subscriptions')

logger = logging.getLogger(__name__)

# 2. 使用蓝图定义路由
@actor_subscriptions_bp.route('/search', methods=['GET'])
@login_required
@processor_ready_required
def api_search_actors():
    # ... (函数逻辑和原来完全一样) ...
    query = request.args.get('name', '').strip()
    if not query:
        return jsonify({"error": "必须提供搜索关键词 'name'"}), 400

    tmdb_api_key = config_manager.APP_CONFIG.get("tmdb_api_key")
    if not tmdb_api_key:
        return jsonify({"error": "服务器未配置TMDb API Key"}), 503

    try:
        search_results = tmdb_handler.search_person_tmdb(query, tmdb_api_key)
        if search_results is None:
            return jsonify({"error": "从TMDb搜索演员时发生错误"}), 500
        
        formatted_results = []
        for person in search_results:
            if person.get('profile_path') and person.get('known_for'):
                 formatted_results.append({
                     "id": person.get("id"), "name": person.get("name"),
                     "profile_path": person.get("profile_path"),
                     "known_for_department": person.get("known_for_department"),
                     "known_for": ", ".join([item.get('title', item.get('name', '')) for item in person.get('known_for', [])])
                 })
        return jsonify(formatted_results)
    except Exception as e:
        logger.error(f"API /api/actor-subscriptions/search 发生错误: {e}", exc_info=True)
        return jsonify({"error": "搜索演员时发生未知的服务器错误"}), 500

@actor_subscriptions_bp.route('', methods=['GET', 'POST'])
@login_required
def handle_actor_subscriptions():
    # ... (函数逻辑和原来完全一样) ...
    if request.method == 'GET':
        try:
            subscriptions = db_handler.get_all_actor_subscriptions(config_manager.DB_PATH)
            return jsonify(subscriptions)
        except Exception as e:
            return jsonify({"error": "获取订阅列表时发生服务器内部错误"}), 500

    if request.method == 'POST':
        data = request.json
        try:
            new_sub_id = db_handler.add_actor_subscription(
                db_path=config_manager.DB_PATH,
                tmdb_person_id=data.get('tmdb_person_id'),
                actor_name=data.get('actor_name'),
                profile_path=data.get('profile_path'),
                config=data.get('config', {})
            )
            return jsonify({"message": f"演员 {data.get('actor_name')} 已成功订阅！", "id": new_sub_id}), 201
        except sqlite3.IntegrityError:
            return jsonify({"error": "该演员已经被订阅过了"}), 409
        except Exception as e:
            return jsonify({"error": "添加订阅时发生服务器内部错误"}), 500

@actor_subscriptions_bp.route('/<int:sub_id>', methods=['GET', 'PUT', 'DELETE'])
@login_required
def handle_single_actor_subscription(sub_id):
    # ... (函数逻辑和原来完全一样) ...
    if request.method == 'GET':
        try:
            response_data = db_handler.get_single_subscription_details(config_manager.DB_PATH, sub_id)
            return jsonify(response_data) if response_data else ({"error": "未找到指定的订阅"}, 404)
        except Exception as e:
            return jsonify({"error": "获取订阅详情时发生服务器内部错误"}), 500
    
    if request.method == 'PUT':
        try:
            success = db_handler.update_actor_subscription(config_manager.DB_PATH, sub_id, request.json)
            return jsonify({"message": "订阅已成功更新！"}) if success else ({"error": "未找到指定的订阅"}, 404)
        except Exception as e:
            return jsonify({"error": "更新订阅时发生服务器内部错误"}), 500

    if request.method == 'DELETE':
        try:
            db_handler.delete_actor_subscription(config_manager.DB_PATH, sub_id)
            return jsonify({"message": "订阅已成功删除。"})
        except Exception as e:
            return jsonify({"error": "删除订阅时发生服务器内部错误"}), 500

@actor_subscriptions_bp.route('/<int:sub_id>/refresh', methods=['POST'])
@login_required
@task_lock_required
def refresh_single_actor_subscription(sub_id):
    # ... (函数逻辑和原来完全一样) ...
    from web_app import task_scan_actor_media # 延迟导入
    actor_name = f"订阅ID {sub_id}" # 简化获取名字的逻辑
    task_manager.submit_task(task_scan_actor_media, f"手动刷新演员: {actor_name}", sub_id)
    return jsonify({"message": f"刷新演员 {actor_name} 作品的任务已提交！"}), 202
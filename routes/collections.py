# routes/collections.py

from flask import Blueprint, request, jsonify
import logging
import json

# 导入需要的模块
import db_handler
import config_manager
import moviepilot_handler
from extensions import login_required, task_lock_required, processor_ready_required

# 1. 创建电影合集蓝图
collections_bp = Blueprint('collections', __name__, url_prefix='/api/collections')

logger = logging.getLogger(__name__)

# 2. 使用蓝图定义路由
@collections_bp.route('/status', methods=['GET'])
@login_required
@processor_ready_required
def api_get_collections_status():
    try:
        final_results = db_handler.get_all_collections(config_manager.DB_PATH)
        return jsonify(final_results)
    except Exception as e:
        logger.error(f"读取合集状态时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "读取合集时发生服务器内部错误"}), 500

@collections_bp.route('/subscribe_all_missing', methods=['POST'])
@login_required
@task_lock_required
def api_subscribe_all_missing():
    # ... (函数逻辑和原来完全一样) ...
    logger.info("API (Blueprint): 收到一键订阅所有缺失电影的请求。")
    total_subscribed_count = 0
    total_failed_count = 0
    
    try:
        collections_to_process = db_handler.get_collections_with_missing_movies(config_manager.DB_PATH)

        if not collections_to_process:
            return jsonify({"message": "没有发现任何缺失的电影需要订阅。", "count": 0}), 200

        for collection in collections_to_process:
            collection_id = collection['emby_collection_id']
            collection_name = collection['name']
            
            try:
                movies = json.loads(collection.get('missing_movies_json', '[]'))
            except (json.JSONDecodeError, TypeError):
                continue

            needs_db_update = False
            for movie in movies:
                if movie.get('status') == 'missing':
                    success = moviepilot_handler.subscribe_movie_to_moviepilot(movie, config_manager.APP_CONFIG)
                    if success:
                        movie['status'] = 'subscribed'
                        total_subscribed_count += 1
                        needs_db_update = True
                    else:
                        total_failed_count += 1
            
            if needs_db_update:
                db_handler.update_collection_movies(config_manager.DB_PATH, collection_id, movies)
        
        message = f"操作完成！成功提交 {total_subscribed_count} 部电影订阅。"
        if total_failed_count > 0:
            message += f" 有 {total_failed_count} 部电影订阅失败，请检查日志。"
        
        return jsonify({"message": message, "count": total_subscribed_count}), 200

    except Exception as e:
        logger.error(f"执行一键订阅时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "服务器在处理一键订阅时发生内部错误"}), 500

@collections_bp.route('/update_movie_status', methods=['POST'])
@login_required
def api_update_movie_status():
    # ... (函数逻辑和原来完全一样) ...
    data = request.json
    collection_id = data.get('collection_id')
    movie_tmdb_id = data.get('movie_tmdb_id')
    new_status = data.get('new_status')

    if not all([collection_id, movie_tmdb_id, new_status]):
        return jsonify({"error": "缺少 collection_id, movie_tmdb_id 或 new_status"}), 400
    
    if new_status not in ['subscribed', 'missing', 'ignored']:
        return jsonify({"error": "无效的状态"}), 400

    try:
        success = db_handler.update_single_movie_status_in_collection(
            db_path=config_manager.DB_PATH,
            collection_id=collection_id,
            movie_tmdb_id=movie_tmdb_id,
            new_status=new_status
        )
        if success:
            return jsonify({"message": "电影状态已成功更新！"}), 200
        else:
            return jsonify({"error": "未在该合集的电影列表中找到指定的电影或合集"}), 404
    except Exception as e:
        logger.error(f"更新电影状态时发生数据库错误: {e}", exc_info=True)
        return jsonify({"error": "服务器在处理请求时发生内部错误"}), 500
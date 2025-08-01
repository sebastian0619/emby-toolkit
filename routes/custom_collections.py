# routes/custom_collections.py

from flask import Blueprint, request, jsonify
import logging
import json
import constants
import db_handler
import config_manager
import task_manager
import moviepilot_handler
import emby_handler
from extensions import login_required

# 1. 创建自定义合集蓝图
custom_collections_bp = Blueprint('custom_collections', __name__, url_prefix='/api/custom_collections')

logger = logging.getLogger(__name__)

# 2. 定义API路由

@custom_collections_bp.route('', methods=['GET']) # 原为 '/'
@login_required
def api_get_all_custom_collections():
    """获取所有自定义合集定义"""
    try:
        # 此函数现在会返回包含健康状态的新字段
        collections = db_handler.get_all_custom_collections(config_manager.DB_PATH)
        return jsonify(collections)
    except Exception as e:
        logger.error(f"获取所有自定义合集时出错: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500

@custom_collections_bp.route('', methods=['POST']) # 原为 '/'
@login_required
def api_create_custom_collection():
    """创建一个新的自定义合集定义 (V3 - 加固版)"""
    try:
        data = request.json
        name = data.get('name')
        type = data.get('type')
        definition = data.get('definition')

        if not all([name, type, definition]):
            return jsonify({"error": "请求无效: 缺少 name, type, 或 definition"}), 400
        if type not in ['filter', 'list']:
            return jsonify({"error": f"请求无效: 不支持的类型 '{type}'"}), 400

        if type == 'list' and not definition.get('url'):
            return jsonify({"error": "榜单导入模式下，definition 必须包含 'url'"}), 400
        if type == 'filter':
            if not isinstance(definition.get('rules'), list) or not definition.get('logic'):
                 return jsonify({"error": "筛选规则模式下，definition 必须包含 'rules' 列表和 'logic'"}), 400
            if not definition['rules']:
                 return jsonify({"error": "筛选规则不能为空"}), 400

        definition_json = json.dumps(definition, ensure_ascii=False)
        
        collection_id = db_handler.create_custom_collection(config_manager.DB_PATH, name, type, definition_json)
        
        if collection_id:
            new_collection = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, collection_id)
            return jsonify(new_collection), 201
        else:
            return jsonify({"error": "数据库操作失败，无法创建合集"}), 500
            
    except Exception as e:
        logger.error(f"创建自定义合集时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误，请检查后端日志"}), 500

@custom_collections_bp.route('/<int:collection_id>', methods=['PUT'])
@login_required
def api_update_custom_collection(collection_id):
    """更新一个自定义合集定义 (V3 - 加固版)"""
    try:
        data = request.json
        name = data.get('name')
        type = data.get('type')
        definition = data.get('definition')
        status = data.get('status')

        if not all([name, type, definition, status]):
            return jsonify({"error": "请求无效: 缺少必要参数"}), 400
        
        if type == 'list' and not definition.get('url'):
            return jsonify({"error": "榜单导入模式下，definition 必须包含 'url'"}), 400
        if type == 'filter':
            if not isinstance(definition.get('rules'), list) or not definition.get('logic'):
                 return jsonify({"error": "筛选规则模式下，definition 必须包含 'rules' 列表和 'logic'"}), 400
            if not definition['rules']:
                 return jsonify({"error": "筛选规则不能为空"}), 400

        definition_json = json.dumps(definition, ensure_ascii=False)
        
        success = db_handler.update_custom_collection(config_manager.DB_PATH, collection_id, name, type, definition_json, status)
        
        if success:
            updated_collection = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, collection_id)
            return jsonify(updated_collection)
        else:
            return jsonify({"error": "数据库操作失败，未找到或无法更新该合集"}), 404
            
    except Exception as e:
        logger.error(f"更新自定义合集 {collection_id} 时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误，请检查后端日志"}), 500

@custom_collections_bp.route('/<int:collection_id>', methods=['DELETE'])
@login_required
def api_delete_custom_collection(collection_id):
    """【安全模式】只删除本地数据库中的自定义合集定义。"""
    try:
        collection_to_delete = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, collection_id)
        if not collection_to_delete:
            # 即使找不到，也返回成功，因为最终结果是一样的
            return jsonify({"message": "合集已删除或不存在。"}), 200

        collection_name = collection_to_delete.get('name', f"ID: {collection_id}")

        # ★★★ 核心修改：只调用数据库删除，不再与Emby交互 ★★★
        db_success = db_handler.delete_custom_collection(
            db_path=config_manager.DB_PATH, 
            collection_id=collection_id
        )

        if db_success:
            logger.info(f"已从本地数据库删除合集 '{collection_name}' 的定义。Emby中的合集实体需要手动清理。")
            return jsonify({"message": f"合集 '{collection_name}' 的本地定义已删除。"}), 200
        else:
            # 理论上，如果前面能找到，这里不应该失败
            return jsonify({"error": "数据库删除操作失败，请查看日志。"}), 500

    except Exception as e:
        logger.error(f"删除自定义合集 {collection_id} 时出错: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500

@custom_collections_bp.route('/<int:collection_id>/sync', methods=['POST'])
@login_required
def api_sync_custom_collection(collection_id):
    """触发对单个自定义合集的后台同步任务"""
    from tasks import task_process_custom_collection

    collection = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, collection_id)
    if not collection:
        return jsonify({"error": "未找到指定的自定义合集"}), 404

    task_name = f"生成自定义合集: {collection['name']}"
    task_manager.submit_task(
        task_process_custom_collection,
        task_name,
        custom_collection_id=collection_id
    )
    
    return jsonify({"message": f"'{task_name}' 任务已提交到后台处理。"}), 202

# --- 一键生成所有自定义合集 ---
@custom_collections_bp.route('/sync_all', methods=['POST'])
@login_required
def api_sync_all_custom_collections():
    """触发对所有已启用的自定义合集进行后台同步的任务"""
    from tasks import task_process_all_custom_collections

    task_name = "一键生成所有自建合集"
    task_manager.submit_task(
        task_process_all_custom_collections,
        task_name
    )
    
    return jsonify({"message": f"'{task_name}' 任务已提交到后台处理。"}), 202

# ★★★ 获取单个自定义合集健康状态的API (升级版) ★★★
@custom_collections_bp.route('/<int:collection_id>/status', methods=['GET'])
@login_required
def api_get_custom_collection_status(collection_id):
    """
    【升级版】获取单个自定义合集的健康状态详情，用于弹窗显示。
    它现在直接查询 custom_collections 表。
    """
    try:
        collection_details = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, collection_id)
        if not collection_details:
            return jsonify({"error": "未在自定义合集表中找到该合集"}), 404
        
        # 解析JSON字段，为前端准备数据
        try:
            collection_details['media_items'] = json.loads(collection_details.get('generated_media_info_json', '[]'))
        except (json.JSONDecodeError, TypeError):
            collection_details['media_items'] = []
        
        # 删除原始的json字段，减少传输数据量
        if 'generated_media_info_json' in collection_details:
            del collection_details['generated_media_info_json']
        if 'definition_json' in collection_details:
             del collection_details['definition_json']
            
        return jsonify(collection_details)
            
    except Exception as e:
        logger.error(f"读取单个自定义合集状态 {collection_id} 时出错: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500

# ★★★ 新增：更新自定义合集中单个媒体项状态的API ★★★
@custom_collections_bp.route('/<int:collection_id>/media_status', methods=['POST'])
@login_required
def api_update_custom_collection_media_status(collection_id):
    """更新自定义合集中单个媒体项的状态 (e.g., subscribed -> missing)"""
    data = request.json
    media_tmdb_id = data.get('tmdb_id')
    new_status = data.get('new_status')

    if not all([media_tmdb_id, new_status]):
        return jsonify({"error": "请求无效: 缺少 tmdb_id 或 new_status"}), 400

    try:
        success = db_handler.update_single_media_status_in_custom_collection(
            db_path=config_manager.DB_PATH,
            collection_id=collection_id,
            media_tmdb_id=str(media_tmdb_id),
            new_status=new_status
        )
        if success:
            return jsonify({"message": "状态更新成功"})
        else:
            return jsonify({"error": "更新失败，未找到对应的媒体项或合集"}), 404
    except Exception as e:
        logger.error(f"更新自定义合集 {collection_id} 中媒体状态时出错: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500
    
# --- 订阅单部剧集 ---
@custom_collections_bp.route('/subscribe', methods=['POST'])
@login_required
def api_subscribe_media_from_custom_collection():
    """
    【新位置】从自建合集页面手动订阅电影或剧集到MoviePilot。
    """
    data = request.json
    tmdb_id = data.get('tmdb_id')
    title = data.get('title')
    item_type = data.get('item_type', 'Movie') 

    if not tmdb_id or not title:
        return jsonify({"error": "请求无效: 缺少 tmdb_id 或 title"}), 400

    logger.info(f"收到来自[自建合集]的手动订阅请求: 类型='{item_type}', 名称='{title}', TMDb ID='{tmdb_id}'")

    success = False
    try:
        if item_type == 'Movie':
            movie_info = {"tmdb_id": tmdb_id, "title": title}
            success = moviepilot_handler.subscribe_movie_to_moviepilot(movie_info, config_manager.APP_CONFIG)
        elif item_type == 'Series':
            series_info = {"tmdb_id": tmdb_id, "item_name": title}
            success = moviepilot_handler.subscribe_series_to_moviepilot(series_info, season_number=None, config=config_manager.APP_CONFIG)
        else:
            return jsonify({"error": f"不支持的订阅类型: '{item_type}'"}), 400

        if success:
            return jsonify({"message": f"《{title}》已成功提交订阅任务。"}), 200
        else:
            return jsonify({"error": "提交到 MoviePilot 失败，请检查日志。"}), 500

    except Exception as e:
        logger.error(f"处理订阅请求时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500
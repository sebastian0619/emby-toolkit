# routes/custom_collections.py

from flask import Blueprint, request, jsonify
import logging
import json

import db_handler
import config_manager
import task_manager
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
    """删除一个自定义合集定义"""
    try:
        success = db_handler.delete_custom_collection(config_manager.DB_PATH, collection_id)
        if success:
            return jsonify({"message": "删除成功"}), 200
        else:
            return jsonify({"error": "删除失败"}), 404
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
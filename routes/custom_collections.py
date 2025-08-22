# routes/custom_collections.py

from flask import Blueprint, request, jsonify
import logging
import sqlite3
import json
import constants
import db_handler
import config_manager
import task_manager
import moviepilot_handler
import emby_handler
from extensions import login_required
from custom_collection_handler import FilterEngine
from utils import get_country_translation_map
# 1. 创建自定义合集蓝图
custom_collections_bp = Blueprint('custom_collections', __name__, url_prefix='/api/custom_collections')

logger = logging.getLogger(__name__)

# 2. 定义API路由

# --- 获取所有自定义合集定义 ---
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

# --- 创建一个新的自定义合集定义 ---
@custom_collections_bp.route('', methods=['POST'])
@login_required
def api_create_custom_collection():
    """创建一个新的自定义合集定义"""
    data = request.json
    name = data.get('name')
    type = data.get('type')
    definition = data.get('definition')

    if not all([name, type, definition]):
        return jsonify({"error": "请求无效: 缺少 name, type, 或 definition"}), 400
    # ... [其他数据验证代码] ...

    definition_json = json.dumps(definition, ensure_ascii=False)
    
    try:
        # ★★★ 将数据库操作包裹在新的 try...except 块中 ★★★
        collection_id = db_handler.create_custom_collection(config_manager.DB_PATH, name, type, definition_json)
        new_collection = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, collection_id)
        return jsonify(new_collection), 201

    except sqlite3.IntegrityError:
        # ★★★ 专门捕获唯一性冲突异常 ★★★
        logger.warning(f"创建自定义合集失败：名称 '{name}' 已存在。")
        # ★★★ 返回一个明确的、用户友好的错误信息，和 409 Conflict 状态码 ★★★
        return jsonify({"error": f"创建失败：名为 '{name}' 的合集已存在。"}), 409

    except Exception as e:
        # ★★★ 捕获所有其他可能的错误（包括db_handler抛出的其他sqlite3.Error） ★★★
        logger.error(f"创建自定义合集 '{name}' 时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "数据库操作失败，无法创建合集，请检查后端日志。"}), 500

# --- 更新一个自定义合集定义 ---
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

# ★★★ 更新合集排序的API ★★★
@custom_collections_bp.route('/update_order', methods=['POST'])
@login_required
def api_update_custom_collections_order():
    """接收前端发来的新顺序并更新到数据库"""
    data = request.json
    ordered_ids = data.get('ids')

    if not isinstance(ordered_ids, list):
        return jsonify({"error": "请求无效: 需要一个ID列表。"}), 400

    try:
        success = db_handler.update_custom_collections_order(config_manager.DB_PATH, ordered_ids)
        if success:
            return jsonify({"message": "合集顺序已成功更新。"}), 200
        else:
            return jsonify({"error": "数据库操作失败，无法更新顺序。"}), 500
    except Exception as e:
        logger.error(f"更新自定义合集顺序时出错: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500

# --- 联动删除Emby合集 ---
@custom_collections_bp.route('/<int:collection_id>', methods=['DELETE'])
@login_required
def api_delete_custom_collection(collection_id):
    """【V8 - 最终决战版】通过清空所有成员来联动删除Emby合集"""
    try:
        # 步骤 1: 获取待删除合集的完整信息
        collection_to_delete = db_handler.get_custom_collection_by_id(config_manager.DB_PATH, collection_id)
        if not collection_to_delete:
            return jsonify({"error": "未找到要删除的合集"}), 404

        emby_id_to_empty = collection_to_delete.get('emby_collection_id')
        collection_name = collection_to_delete.get('name')

        # 步骤 2: 如果存在关联的Emby ID，则调用Emby Handler，清空其内容
        if emby_id_to_empty:
            logger.info(f"  -> 正在删除合集 '{collection_name}' (Emby ID: {emby_id_to_empty})...")
            
            # ★★★ 调用我们全新的、真正有效的清空函数 ★★★
            emby_handler.empty_collection_in_emby(
                collection_id=emby_id_to_empty,
                base_url=config_manager.APP_CONFIG.get('emby_server_url'),
                api_key=config_manager.APP_CONFIG.get('emby_api_key'),
                user_id=config_manager.APP_CONFIG.get('emby_user_id')
            )

        # 步骤 3: 无论Emby端是否成功，都删除本地数据库中的记录
        db_success = db_handler.delete_custom_collection(
            db_path=config_manager.DB_PATH, 
            collection_id=collection_id
        )

        if db_success:
            return jsonify({"message": f"自定义合集 '{collection_name}' 已成功联动删除。"}), 200
        else:
            return jsonify({"error": "数据库删除操作失败，请查看日志。"}), 500

    except Exception as e:
        logger.error(f"删除自定义合集 {collection_id} 时出错: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500

# --- 获取单个自定义合集健康状态 ---
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

# --- 更新自定义合集中单个媒体项状态 ---
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
    
# --- 手动订阅 ---
@custom_collections_bp.route('/subscribe', methods=['POST'])
@login_required
def api_subscribe_media_from_custom_collection():
    """
    【V9 - 终极防御版】从RSS榜单合集页面手动订阅。
    此版本增加了对 item_type 字段的终极防御性解析，无论其在数据库中
    被存储为字符串 ('Series') 还是列表 (['Series'])，都能正确识别权威类型。
    """
    data = request.json
    tmdb_id = data.get('tmdb_id')
    collection_id = data.get('collection_id')

    if not all([tmdb_id, collection_id]):
        return jsonify({"error": "请求无效: 缺少 tmdb_id 或 collection_id"}), 400

    try:
        # --- 步骤 1: 从数据库获取合集的权威定义和具体媒体的标题 ---
        with db_handler.get_db_connection(config_manager.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT definition_json, generated_media_info_json FROM custom_collections WHERE id = ?", (collection_id,))
            collection_record = cursor.fetchone()

            if not collection_record:
                return jsonify({"error": "数据库错误: 找不到指定的合集。"}), 404

            # --- ▼▼▼ 核心修复：对 item_type 进行终极防御性解析 ▼▼▼ ---
            definition = json.loads(collection_record['definition_json'])
            item_type_from_db = definition.get('item_type', 'Movie') # 默认为 'Movie'

            authoritative_type = None
            if isinstance(item_type_from_db, list):
                # 如果是列表 (e.g., ['Series']), 取第一个元素
                if item_type_from_db:
                    authoritative_type = item_type_from_db[0]
            elif isinstance(item_type_from_db, str):
                # 如果是字符串 (e.g., 'Series'), 直接使用
                authoritative_type = item_type_from_db
            
            # 如果经过解析后仍然无法确定或类型不正确，则提供默认值并记录警告
            if authoritative_type not in ['Movie', 'Series']:
                logger.warning(f"合集 {collection_id} 的 item_type 格式无法识别 ('{item_type_from_db}')，将默认使用 'Movie' 进行订阅。")
                authoritative_type = 'Movie'
            # --- ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ ---

            # 从【生成结果】中获取媒体的标题 (这部分逻辑是正确的)
            media_list = json.loads(collection_record['generated_media_info_json'] or '[]')
            target_media_item = next((item for item in media_list if str(item.get('tmdb_id')) == str(tmdb_id)), None)

            if not target_media_item:
                return jsonify({"error": "订阅失败: 在该合集的媒体列表中未找到此项目。"}), 404
            
            authoritative_title = target_media_item.get('title')
            if not authoritative_title:
                return jsonify({"error": "订阅失败: 数据库中的媒体信息不完整（缺少标题）。"}), 500

        # --- 步骤 2: 使用“权威类型”和“具体标题”执行订阅 ---
        type_map = {
            'Movie': '电影',
            'Series': '电视剧'
        }

        logger.info(f"  -> 依据合集定义，使用类型 '{type_map.get(authoritative_type, authoritative_type)}' 为《{authoritative_title}》(TMDb ID: {tmdb_id}) 发起订阅...")
        
        success = False
        if authoritative_type == 'Movie':
            movie_info = {"tmdb_id": tmdb_id, "title": authoritative_title}
            success = moviepilot_handler.subscribe_movie_to_moviepilot(movie_info, config_manager.APP_CONFIG)
        elif authoritative_type == 'Series':
            series_info = {"tmdb_id": tmdb_id, "title": authoritative_title}
            success = moviepilot_handler.subscribe_series_to_moviepilot(series_info, season_number=None, config=config_manager.APP_CONFIG)
        
        if not success:
            return jsonify({"error": "提交到 MoviePilot 失败，请检查日志。"}), 500

        # --- 步骤 3: 订阅成功后，更新数据库中的状态 ---
        target_media_item['status'] = 'subscribed'

        with db_handler.get_db_connection(config_manager.DB_PATH) as conn:
            cursor = conn.cursor()
            new_missing_count = sum(1 for item in media_list if item.get('status') == 'missing')
            new_health_status = 'has_missing' if new_missing_count > 0 else 'ok'
            new_media_info_json = json.dumps(media_list, ensure_ascii=False)

            cursor.execute(
                "UPDATE custom_collections SET generated_media_info_json = ?, health_status = ?, missing_count = ? WHERE id = ?",
                (new_media_info_json, new_health_status, new_missing_count, collection_id)
            )
            conn.commit()
            logger.info(f"  -> 已成功更新合集 {collection_id} 中《{authoritative_title}》的状态为 '订阅中'。")

        return jsonify({"message": f"《{authoritative_title}》已成功提交订阅，并已更新本地状态。"}), 200

    except Exception as e:
        logger.error(f"处理订阅请求时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "处理订阅时发生服务器内部错误。"}), 500
    
# --- 根据关键词搜索演员 ---
@custom_collections_bp.route('/search_actors') # 或者 @media_api_bp.route('/search_actors')
@login_required
def api_search_actors():
    search_term = request.args.get('q', '')
    if len(search_term) < 1:
        return jsonify([])
    
    try:
        actors = db_handler.search_unique_actors(config_manager.DB_PATH, search_term)
        # 返回简单的字符串列表
        return jsonify(actors)
    except Exception as e:
        logger.error(f"搜索演员API出错: {e}", exc_info=True)
        return jsonify({"error": "服务器内部错误"}), 500
    
# --- 提取国家列表 ---
@custom_collections_bp.route('/config/countries', methods=['GET'])
@login_required
def api_get_countries_for_filter():
    """【重构版】为筛选器提供一个纯中文的国家/地区列表。"""
    try:
        # get_country_translation_map 返回 {'英文': '中文', ...}
        # 我们需要的是所有的中文值
        full_map = get_country_translation_map()
        # 使用 set 去重，然后排序
        chinese_names = sorted(list(set(full_map.values())))
        return jsonify(chinese_names)
    except Exception as e:
        logger.error(f"获取国家/地区列表时出错: {e}", exc_info=True)
        return jsonify([]), 500
    
# --- 提取标签列表 ---
@custom_collections_bp.route('/config/tags', methods=['GET'])
@login_required
def api_get_tags_for_filter():
    """为筛选器提供一个标签列表。"""
    try:
        tags = db_handler.get_unique_tags(config_manager.DB_PATH)
        return jsonify(tags)
    except Exception as e:
        logger.error(f"获取标签列表时出错: {e}", exc_info=True)
        return jsonify([]), 500

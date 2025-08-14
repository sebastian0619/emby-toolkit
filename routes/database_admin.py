# routes/database_admin.py

from flask import Blueprint, request, jsonify, Response
import logging
import json
import re
import os
import time
from datetime import datetime
import sqlite3

# 导入底层模块
import db_handler
import config_manager
import task_manager
import constants

# 导入共享模块
import extensions
from extensions import login_required, processor_ready_required, task_lock_required

# 1. 创建蓝图
db_admin_bp = Blueprint('database_admin', __name__, url_prefix='/api')
logger = logging.getLogger(__name__)

def _count_table_rows(cursor: sqlite3.Cursor, table_name: str, condition: str = "") -> int:
    """一个通用的表行数计数辅助函数，增加错误处理。"""
    try:
        # 使用参数化查询防止SQL注入，即使表名是内部控制的
        query = f"SELECT COUNT(*) FROM {table_name}"
        if condition:
            # 注意：这里的condition仍然是直接拼接，因为它可能包含复杂的逻辑
            # 但在调用此函数时，应确保condition的内容是安全的
            query += f" WHERE {condition}"
        cursor.execute(query)
        result = cursor.fetchone()
        return result[0] if result else 0
    except sqlite3.Error as e:
        logger.error(f"计算表 '{table_name}' 行数时出错: {e}")
        return -1 # 返回-1表示错误

# 2. 定义路由

# --- 数据库表管理 ---
@db_admin_bp.route('/database/tables', methods=['GET'])
@login_required
def api_get_db_tables():
    try:
        with db_handler.get_db_connection(config_manager.DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [row[0] for row in cursor.fetchall()]
        return jsonify(tables)
    except Exception as e:
        logger.error(f"获取数据库表列表时出错: {e}", exc_info=True)
        return jsonify({"error": "无法获取数据库表列表"}), 500

@db_admin_bp.route('/database/export', methods=['POST'])
@login_required
def api_export_database():
    try:
        tables_to_export = request.json.get('tables')
        if not tables_to_export or not isinstance(tables_to_export, list):
            return jsonify({"error": "请求体中必须包含一个 'tables' 数组"}), 400

        backup_data = {
            "metadata": {
                "export_date": datetime.utcnow().isoformat() + "Z",
                "app_version": constants.APP_VERSION,
                "source_emby_server_id": extensions.EMBY_SERVER_ID,
                "tables": tables_to_export
            }, "data": {}
        }

        with db_handler.get_db_connection(config_manager.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            for table_name in tables_to_export:
                if not re.match(r'^[a-zA-Z0-9_]+$', table_name):
                     continue
                cursor.execute(f"SELECT * FROM {table_name}")
                rows = cursor.fetchall()
                backup_data["data"][table_name] = [dict(row) for row in rows]

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"database_backup_{timestamp}.json"
        json_output = json.dumps(backup_data, indent=2, ensure_ascii=False)

        response = Response(json_output, mimetype='application/json; charset=utf-8')
        response.headers.set("Content-Disposition", "attachment", filename=filename)
        return response
    except Exception as e:
        logger.error(f"导出数据库时发生错误: {e}", exc_info=True)
        return jsonify({"error": f"导出时发生服务器错误: {e}"}), 500

@db_admin_bp.route('/database/import', methods=['POST'])
@login_required
@task_lock_required
def api_import_database():
    """
    【通用队列版】接收备份文件、要导入的表名列表以及导入模式，
    并提交一个后台任务来处理恢复。
    """
    from tasks import task_import_database
    if 'file' not in request.files:
        return jsonify({"error": "请求中未找到文件部分"}), 400
    
    file = request.files['file']
    if not file.filename or not file.filename.endswith('.json'):
        return jsonify({"error": "未选择文件或文件类型必须是 .json"}), 400

    tables_to_import_str = request.form.get('tables')
    if not tables_to_import_str:
        return jsonify({"error": "必须通过 'tables' 字段指定要导入的表"}), 400
    tables_to_import = [table.strip() for table in tables_to_import_str.split(',')]

    # ★ 关键：从表单获取导入模式，默认为 'merge'，更安全 ★
    import_mode = request.form.get('mode', 'merge').lower()
    if import_mode not in ['overwrite', 'merge']:
        return jsonify({"error": "无效的导入模式。只支持 'overwrite' 或 'merge'"}), 400
    
    mode_translations = {
        'overwrite': '本地恢复模式',
        'merge': '共享合并模式',
    }
    # 使用 .get() 以防万一，如果找不到就用回英文原名
    import_mode_cn = mode_translations.get(import_mode, import_mode)

    try:
        file_content = file.stream.read().decode("utf-8-sig")
        # ▼▼▼ 新增的安全校验逻辑 ▼▼▼
        backup_json = json.loads(file_content)
        backup_metadata = backup_json.get("metadata", {})
        backup_server_id = backup_metadata.get("source_emby_server_id")

        # 只对最危险的“本地恢复”模式进行强制校验
        if import_mode == 'overwrite':
            # 检查1：备份文件必须有ID指纹
            if not backup_server_id:
                error_msg = "此备份文件缺少来源服务器ID，为安全起见，禁止使用“本地恢复”模式导入，这通常意味着它是一个旧版备份，请使用“共享合并”模式。"
                logger.warning(f"禁止导入: {error_msg}")
                return jsonify({"error": error_msg}), 403 # 403 Forbidden

            # 检查2：当前服务器必须能获取到ID
            current_server_id = extensions.EMBY_SERVER_ID
            if not current_server_id:
                error_msg = "无法获取当前Emby服务器的ID，可能连接已断开。为安全起见，暂时禁止使用“本地恢复”模式。"
                logger.warning(f"禁止导入: {error_msg}")
                return jsonify({"error": error_msg}), 503 # 503 Service Unavailable

            # 检查3：两个ID必须完全匹配
            if backup_server_id != current_server_id:
                error_msg = (f"服务器ID不匹配！此备份来自另一个Emby服务器，"
                           "直接使用“本地恢复”会造成数据严重混乱。操作已禁止。\n\n"
                           f"备份来源ID: ...{backup_server_id[-12:]}\n"
                           f"当前服务器ID: ...{current_server_id[-12:]}\n\n"
                           "如果你确实想合并数据，请改用“共享合并”模式。")
                logger.warning(f"禁止导入: {error_msg}")
                return jsonify({"error": error_msg}), 403 # 403 Forbidden
        # ▲▲▲ 安全校验逻辑结束 ▲▲▲
        logger.trace(f"已接收上传的备份文件 '{file.filename}'，将以 '{import_mode_cn}' 模式导入表: {tables_to_import}")

        success = task_manager.submit_task(
            task_import_database,  # ★ 调用新的后台任务函数
            f"以 {import_mode_cn} 模式恢复数据库表",
            # 传递任务所需的所有参数
            file_content=file_content,
            tables_to_import=tables_to_import,
            import_mode=import_mode
        )
        
        return jsonify({"message": f"文件上传成功，已提交后台任务以 '{import_mode_cn}' 模式恢复 {len(tables_to_import)} 个表。"}), 202

    except Exception as e:
        logger.error(f"处理数据库导入请求时发生错误: {e}", exc_info=True)
        return jsonify({"error": "处理上传文件时发生服务器错误"}), 500

# --- 数据看板 ---
@db_admin_bp.route('/database/stats', methods=['GET'])
@login_required
def api_get_database_stats():
    """
    从数据库中查询并聚合各种统计数据，用于在UI上展示。
    """
    stats = {}
    try:
        with db_handler.get_db_connection(config_manager.DB_PATH) as conn:
            cursor = conn.cursor()

            # --- 1. 核心媒体元数据统计 ---
            stats['media_metadata'] = {
                "total": _count_table_rows(cursor, "media_metadata"),
                "movies": _count_table_rows(cursor, "media_metadata", "item_type = 'Movie'"),
                "series": _count_table_rows(cursor, "media_metadata", "item_type = 'Series'"),
                # ★★★ 新增：用户统计 ★★★
                "users": _count_table_rows(cursor, "users"),
            }

            # --- 2. 合集管理统计 ---
            stats['collections'] = {
                "total_tmdb_collections": _count_table_rows(cursor, "collections_info"),
                "collections_with_missing": _count_table_rows(cursor, "collections_info", "has_missing = 1"),
                "total_custom_collections": _count_table_rows(cursor, "custom_collections", "status = 'active'"),
            }

            # --- 3. 订阅服务统计 ---
            stats['subscriptions'] = {
                "watchlist_active": _count_table_rows(cursor, "watchlist", "status = 'Watching'"),
                "watchlist_paused": _count_table_rows(cursor, "watchlist", "status = 'Paused'"),
                "watchlist_ended": _count_table_rows(cursor, "watchlist", "status = 'Completed'"),
                "actor_subscriptions_active": _count_table_rows(cursor, "actor_subscriptions", "status = 'active'"),
                "tracked_media_total": _count_table_rows(cursor, "tracked_actor_media"),
                "tracked_media_in_library": _count_table_rows(cursor, "tracked_actor_media", "status = 'IN_LIBRARY'"),
            }

            # --- 4. 系统与缓存统计 ---
            db_size_bytes = os.path.getsize(config_manager.DB_PATH)
            stats['system'] = {
                "actor_mappings_count": _count_table_rows(cursor, "person_identity_map"),
                "translation_cache_count": _count_table_rows(cursor, "translation_cache"),
                "processed_log_count": _count_table_rows(cursor, "processed_log"),
                "failed_log_count": _count_table_rows(cursor, "failed_log"),
            }

        return jsonify({"status": "success", "data": stats})

    except Exception as e:
        logger.error(f"获取数据库统计信息时发生严重错误: {e}", exc_info=True)
        return jsonify({"error": "获取数据库统计信息时发生服务器内部错误"}), 500

# --- 待复核列表管理 ---
@db_admin_bp.route('/review_items', methods=['GET'])
@login_required
def api_get_review_items():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    query_filter = request.args.get('query', '', type=str).strip()
    try:
        items, total = db_handler.get_review_items_paginated(config_manager.DB_PATH, page, per_page, query_filter)
        total_pages = (total + per_page - 1) // per_page if total > 0 else 0
        return jsonify({
            "items": items, "total_items": total, "total_pages": total_pages,
            "current_page": page, "per_page": per_page, "query": query_filter
        })
    except Exception as e:
        return jsonify({"error": "获取待复核列表时发生服务器内部错误"}), 500

@db_admin_bp.route('/actions/mark_item_processed/<item_id>', methods=['POST'])
@login_required
def api_mark_item_processed(item_id):
    if task_manager.is_task_running(): return jsonify({"error": "后台有任务正在运行，请稍后再试。"}), 409
    try:
        success = db_handler.mark_review_item_as_processed(config_manager.DB_PATH, item_id)
        if success: return jsonify({"message": f"项目 {item_id} 已成功标记为已处理。"}), 200
        else: return jsonify({"error": f"未在待复核列表中找到项目 {item_id}。"}), 404
    except Exception as e:
        return jsonify({"error": "服务器内部错误"}), 500

# ✨✨✨ 清空待复核列表（并全部标记为已处理）的 API ✨✨✨
@db_admin_bp.route('/actions/clear_review_items', methods=['POST'])
@login_required
@task_lock_required
def api_clear_review_items():
    try:
        count = db_handler.clear_all_review_items(config_manager.DB_PATH)
        if count > 0:
            message = f"操作成功！已将 {count} 个项目移至已处理列表。"
        else:
            message = "操作完成，待复核列表本就是空的。"
        return jsonify({"message": message}), 200
    except Exception as e:
        logger.error("API调用api_clear_review_items时发生错误", exc_info=True)
        return jsonify({"error": "服务器在处理时发生内部错误"}), 500

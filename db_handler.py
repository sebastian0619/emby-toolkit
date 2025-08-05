# db_handler.py
import sqlite3
import json
from datetime import date, timedelta, datetime
import logging
from typing import Optional, Dict, Any, List, Tuple
from flask import jsonify
import emby_handler
logger = logging.getLogger(__name__)

# ======================================================================
# 模块 1: 数据库管理器 (The Unified Data Access Layer)
# ======================================================================

# 数据库表结构。
TABLE_PRIMARY_KEYS = {
    "person_identity_map": "tmdb_person_id",
    "ActorMetadata": "tmdb_id",
    "translation_cache": "original_text",
    "collections_info": "emby_collection_id",
    "watchlist": "item_id",
    "actor_subscriptions": "tmdb_person_id",
    "tracked_actor_media": ("subscription_id", "tmdb_media_id"),
    "processed_log": "item_id",
    "failed_log": "item_id",
    "users": "username",
}

def get_db_connection(db_path: str) -> sqlite3.Connection:
    """
    【中央函数】获取一个配置好 WAL 模式和 row_factory 的数据库连接。
    这是整个应用获取数据库连接的唯一入口。
    """
    if not db_path:
        logger.error("尝试获取数据库连接，但未提供 db_path。")
        raise ValueError("数据库路径 (db_path) 不能为空。")
        
    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn
    except sqlite3.Error as e:
        logger.error(f"获取数据库连接失败: {e}", exc_info=True)
        raise

# ======================================================================
# 模块 2: 演员数据访问层 (Actor Data Access Layer)
# ======================================================================

class ActorDBManager:
    """
    一个专门负责与演员身份相关的数据库表进行交互的类。
    这是所有数据库操作的唯一入口，确保逻辑统一。
    """
    def __init__(self, db_path: str):
        self.db_path = db_path
        logger.trace(f"ActorDBManager 初始化，使用数据库: {self.db_path}")

    def get_translation_from_db(self, cursor: sqlite3.Cursor, text: str, by_translated_text: bool = False) -> Optional[Dict[str, Any]]:
        """
        【已迁移】从数据库获取翻译缓存。
        :param cursor: 必须提供外部数据库游标。
        :param text: 要查询的文本 (可以是原文或译文)。
        :param by_translated_text: 如果为 True，则通过译文反查原文。
        :return: 包含原文、译文和引擎的字典，或 None。
        """
        try:
            # 根据查询模式选择不同的SQL语句
            if by_translated_text:
                sql = "SELECT original_text, translated_text, engine_used FROM translation_cache WHERE translated_text = ?"
            else:
                sql = "SELECT original_text, translated_text, engine_used FROM translation_cache WHERE original_text = ?"

            cursor.execute(sql, (text,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"DB读取翻译缓存失败 for '{text}' (by_translated: {by_translated_text}): {e}", exc_info=True)
            return None

    def save_translation_to_db(self, cursor: sqlite3.Cursor, original_text: str, translated_text: Optional[str], engine_used: Optional[str]):
        """
        【已迁移】将翻译结果保存到数据库。
        :param cursor: 必须提供外部数据库游标。
        """
        try:
            cursor.execute(
                "REPLACE INTO translation_cache (original_text, translated_text, engine_used, last_updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (original_text, translated_text, engine_used)
            )
            logger.trace(f"翻译缓存存DB: '{original_text}' -> '{translated_text}' (引擎: {engine_used})")
        except Exception as e:
            logger.error(f"DB保存翻译缓存失败 for '{original_text}': {e}", exc_info=True)

    def find_person_by_any_id(self, cursor: sqlite3.Cursor, **kwargs) -> Optional[sqlite3.Row]:
        search_criteria = [
            ("tmdb_person_id", kwargs.get("tmdb_id")),
            ("emby_person_id", kwargs.get("emby_id")),
            ("imdb_id", kwargs.get("imdb_id")),
            ("douban_celebrity_id", kwargs.get("douban_celebrity_id")),
        ]
        for column, value in search_criteria:
            if not value: continue
            try:
                cursor.execute(f"SELECT * FROM person_identity_map WHERE {column} = ?", (value,))
                result = cursor.fetchone()
                if result:
                    logger.debug(f"通过 {column}='{value}' 找到了演员记录 (map_id: {result['map_id']})。")
                    return result
            except sqlite3.Error as e:
                logger.error(f"查询 person_identity_map 时出错 ({column}={value}): {e}")
        return None

    def upsert_person(self, cursor: sqlite3.Cursor, person_data: Dict[str, Any], **kwargs):
        """
        【V5 - Create New ID 最终版】
        根据业务流程定制：person_identity_map 是一个独立的ID转换器，map_id不被外部引用。
        因此，采用“融合-删除-创建”策略，逻辑最简单且能从根本上避免所有合并冲突。
        """
        # 1. 标准化和清理输入数据 (不变)
        data_to_process = {
            "primary_name": str(person_data.get("name") or '').strip(),
            "emby_person_id": str(person_data.get("emby_id") or '').strip() or None,
            "tmdb_person_id": str(person_data.get("tmdb_id") or '').strip() or None,
            "imdb_id": str(person_data.get("imdb_id") or '').strip() or None,
            "douban_celebrity_id": str(person_data.get("douban_id") or '').strip() or None,
        }
        id_fields = ["emby_person_id", "tmdb_person_id", "imdb_id", "douban_celebrity_id"]
        provided_ids = {k: v for k, v in data_to_process.items() if k in id_fields and v}

        if not data_to_process["primary_name"] and not provided_ids:
            return -1

        # 2. 收集所有潜在关联记录
        query_parts = []
        query_values = []

        if provided_ids:
            # ✨ 如果有任何ID，则只用ID进行查找 ✨
            for key, value in provided_ids.items():
                query_parts.append(f"{key} = ?")
                query_values.append(value)
        elif data_to_process["primary_name"]:
            # ✨ 只有在完全没有ID时，才退而求其次使用名字查找 ✨
            logger.debug(f"数据无ID，将使用名字 '{data_to_process['primary_name']}' 进行查找。")
            query_parts.append("primary_name = ?")
            query_values.append(data_to_process["primary_name"])
        else:
            # 既没ID也没名字，无法处理
            return -1

        sql_find_candidates = f"SELECT * FROM person_identity_map WHERE {' OR '.join(query_parts)}"
        cursor.execute(sql_find_candidates, tuple(query_values))
        candidate_records = [dict(row) for row in cursor.fetchall()]

        # --- 核心逻辑：融合、删除、创建 ---
        try:
            all_sources = candidate_records + [data_to_process]

            # 3. 融合所有信息，并在比较前进行类型标准化
            final_merged_data = {}
            for key in id_fields:
                # ▼▼▼ 核心修复：在放入集合前，将所有值转换为字符串！ ▼▼▼
                all_values_for_key = {
                    str(source.get(key))
                    for source in all_sources
                    if source.get(key) is not None and str(source.get(key)).strip()
                }
                # ▲▲▲ 这样可以确保 17401 和 '17401' 都变成 '17401'，从而被集合正确去重 ▲▲▲

                if len(all_values_for_key) > 1:
                    logger.trace(
                        f"数据合并冲突！演员 '{data_to_process.get('name')}' 的 '{key}' 存在多个不同的值: {list(all_values_for_key)}。已中止操作。"
                    )
                    return -1
                elif len(all_values_for_key) == 1:
                    final_merged_data[key] = all_values_for_key.pop()

            # 确定最终的名字
            # 优先使用本次传入的、非空的名字，否则从豆瓣记录里找一个
            all_names = {source.get('primary_name') for source in all_sources if source.get('primary_name')}
            final_merged_data['primary_name'] = data_to_process.get("primary_name") or (list(all_names)[0] if all_names else "未知演员")

            # 4. 执行数据库操作：先删除，后创建
            # 4.1 删除所有找到的旧记录
            if candidate_records:
                ids_to_delete = [r['map_id'] for r in candidate_records]
                placeholders = ','.join('?' * len(ids_to_delete))
                sql_delete = f"DELETE FROM person_identity_map WHERE map_id IN ({placeholders})"
                cursor.execute(sql_delete, tuple(ids_to_delete))

            # 4.2 插入一条全新的、完美融合的记录
            cols_to_insert = list(final_merged_data.keys())
            vals_to_insert = list(final_merged_data.values())

            # 确保 primary_name 总是存在
            if 'primary_name' not in cols_to_insert:
                cols_to_insert.append('primary_name')
                vals_to_insert.append("未知演员")

            cols_to_insert.extend(["last_synced_at", "last_updated_at"])
            placeholders = ["?" for _ in vals_to_insert] + ["CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP"]

            sql_insert = f"INSERT INTO person_identity_map ({', '.join(cols_to_insert)}) VALUES ({', '.join(placeholders)})"
            cursor.execute(sql_insert, tuple(vals_to_insert))

            new_map_id = cursor.lastrowid

            return new_map_id

        except sqlite3.IntegrityError as e:
            # 这个异常现在只可能在极端的并发情况下发生，作为最后的保险
            logger.error(f"为演员 '{data_to_process.get('name')}' 创建新记录时发生意外的数据库唯一性冲突: {e}。")
            return -1
        
# ======================================================================
# 模块 3: 日志表数据访问 (Log Tables Data Access)
# ======================================================================

def get_review_items_paginated(db_path: str, page: int, per_page: int, query_filter: str) -> Tuple[List, int]:
    """
    从数据库获取待复核项目列表（分页）。
    返回 (项目列表, 总项目数)。
    """
    offset = (page - 1) * per_page
    items_to_review = []
    total_matching_items = 0
    
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            
            where_clause = ""
            sql_params = []

            if query_filter:
                where_clause = "WHERE item_name LIKE ?"
                sql_params.append(f"%{query_filter}%")

            count_sql = f"SELECT COUNT(*) FROM failed_log {where_clause}"
            cursor.execute(count_sql, tuple(sql_params))
            count_row = cursor.fetchone()
            if count_row:
                total_matching_items = count_row[0]

            items_sql = f"""
                SELECT item_id, item_name, failed_at, reason, item_type, score 
                FROM failed_log 
                {where_clause}
                ORDER BY failed_at DESC 
                LIMIT ? OFFSET ?
            """
            params_for_page_query = sql_params + [per_page, offset]
            cursor.execute(items_sql, tuple(params_for_page_query))
            
            items_to_review = [dict(row) for row in cursor.fetchall()]
            
        return items_to_review, total_matching_items
    except Exception as e:
        logger.error(f"DB: 获取待复核列表失败: {e}", exc_info=True)
        # 向上抛出异常，让调用者处理
        raise


def mark_review_item_as_processed(db_path: str, item_id: str) -> bool:
    """
    将单个待复核项目标记为已处理。
    - 从 failed_log 删除。
    - 将信息添加到 processed_log。
    返回 True 表示成功，False 表示项目未找到。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("BEGIN TRANSACTION;")
            
            cursor.execute("SELECT item_name, item_type, score FROM failed_log WHERE item_id = ?", (item_id,))
            failed_item_info = cursor.fetchone()
            
            if not failed_item_info:
                conn.rollback()
                return False # 项目不存在

            cursor.execute("DELETE FROM failed_log WHERE item_id = ?", (item_id,))
            
            score_to_save = failed_item_info["score"] if failed_item_info["score"] is not None else 10.0
            item_name = failed_item_info["item_name"]
            
            cursor.execute(
                "REPLACE INTO processed_log (item_id, item_name, processed_at, score) VALUES (?, ?, CURRENT_TIMESTAMP, ?)",
                (item_id, item_name, score_to_save)
            )
            
            conn.commit()
            logger.info(f"DB: 项目 {item_id} ('{item_name}') 已成功移至已处理日志。")
            return True
    except Exception as e:
        logger.error(f"DB: 标记项目 {item_id} 为已处理时失败: {e}", exc_info=True)
        raise


def clear_all_review_items(db_path: str) -> int:
    """
    【V3 - 健壮版】清空所有待复核项目，并将它们全部标记为已处理。
    采用“先查询，后操作”的模式，保证“写入”与“删除”的原子性。
    返回被成功处理的项目数量。
    出现错误时抛出异常。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()

            # 1. 第一步：锁定目标
            # 将所有需要移动的项目完整地查询到内存中。
            # `items_to_move` 现在是我们唯一信任的“操作清单”。
            cursor.execute("SELECT item_id, item_name, score FROM failed_log")
            items_to_move = cursor.fetchall()
            
            initial_count = len(items_to_move)
            if initial_count == 0:
                logger.info("操作完成，待复核列表本就是空的。")
                return 0

            logger.info(f"查询到 {initial_count} 条待复核记录，准备开始移动...")

            # 2. 第二步：准备“写入”的数据
            # 根据我们的“操作清单”，创建准备写入 `processed_log` 的精确数据。
            data_for_processed_log = [
                (row['item_id'], row['item_name'], row['score'] or 10.0)
                for row in items_to_move
            ]

            # 3. 第三步：在“保险箱”（事务）中执行所有操作
            try:
                # 3.1 执行写入：将我们准备好的数据写入 `processed_log`
                # 这一步如果失败（比如表结构不对、约束冲突等），会立刻抛出异常，
                # 然后被下面的 `except` 捕获，直接进入回滚流程。
                copy_sql = """
                    REPLACE INTO processed_log (item_id, item_name, score, processed_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """
                cursor.executemany(copy_sql, data_for_processed_log)
                
                # 3.2 执行删除：清空 `failed_log` 表
                # 只有在写入成功后，才会执行到这一步。
                cursor.execute("DELETE FROM failed_log")
                deleted_count = cursor.rowcount # 对于简单的 DELETE，rowcount 是可靠的

                # 4. 第四步：进行最终的、可靠的验证
                # 我们只验证一件事：删除的行数，是否等于我们一开始想要处理的行数？
                if deleted_count == initial_count:
                    # 如果相等，说明从我们查询到删除的这段时间里，没有其他程序干扰 `failed_log` 表。
                    # 同时，因为写入操作没有抛出异常，所以我们可以确信写入也成功了。
                    # 万无一失，提交事务！
                    conn.commit() 
                    logger.info(f"成功移动 {deleted_count} 条记录，事务已提交。")
                    return deleted_count
                else:
                    # 如果不相等，说明发生了并发问题，必须回滚以保证数据安全。
                    conn.rollback()
                    logger.error(f"数据不一致，事务回滚！初始查询到 {initial_count} 条，但最终删除了 {deleted_count} 条。")
                    raise Exception("数据不一致，操作回滚！")

            except Exception as e_inner:
                # 捕获事务中的任何失败，回滚并报告错误。
                logger.error(f"事务执行失败，正在回滚: {e_inner}", exc_info=True)
                conn.rollback()
                raise

    except Exception as e_outer:
        logger.error(f"清空并标记待复核列表时发生顶层异常：{e_outer}", exc_info=True)
        raise
# ======================================================================
# 模块 4: 智能追剧列表数据访问 (Watchlist Data Access)
# ======================================================================

def get_all_watchlist_items(db_path: str) -> List[Dict[str, Any]]:
    """获取所有追剧列表中的项目。"""
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM watchlist ORDER BY added_at DESC")
            items = [dict(row) for row in cursor.fetchall()]
            return items
    except Exception as e:
        logger.error(f"DB: 获取追剧列表失败: {e}", exc_info=True)
        raise


def get_watchlist_item_name(db_path: str, item_id: str) -> Optional[str]:
    """根据 item_id 获取单个追剧项目的名称。"""
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT item_name FROM watchlist WHERE item_id = ?", (item_id,))
            row = cursor.fetchone()
            return row['item_name'] if row else None
    except Exception as e:
        logger.warning(f"DB: 获取项目 {item_id} 名称时出错: {e}")
        return None


def add_item_to_watchlist(db_path: str, item_id: str, tmdb_id: str, item_name: str, item_type: str) -> bool:
    """
    添加一个新项目到追剧列表。
    如果项目已存在，则会替换它。
    返回 True 表示成功。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO watchlist (item_id, tmdb_id, item_name, item_type, status, last_checked_at)
                VALUES (?, ?, ?, ?, 'Watching', NULL)
            """, (item_id, tmdb_id, item_name, item_type))
            conn.commit()
            logger.info(f"DB: 项目 '{item_name}' (ID: {item_id}) 已成功添加/更新到追剧列表。")
            return True
    except Exception as e:
        logger.error(f"DB: 手动添加项目到追剧列表时发生错误: {e}", exc_info=True)
        raise


def update_watchlist_item_status(db_path: str, item_id: str, new_status: str) -> bool:
    """
    更新追剧列表中某个项目的状态。
    返回 True 表示成功，False 表示项目未找到。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE watchlist SET status = ? WHERE item_id = ?",
                (new_status, item_id)
            )
            conn.commit()
            if cursor.rowcount > 0:
                logger.info(f"DB: 项目 {item_id} 的追剧状态已更新为 '{new_status}'。")
                return True
            else:
                logger.warning(f"DB: 尝试更新追剧状态，但未在列表中找到项目 {item_id}。")
                return False
    except Exception as e:
        logger.error(f"DB: 更新追剧状态时发生错误: {e}", exc_info=True)
        raise


def remove_item_from_watchlist(db_path: str, item_id: str) -> bool:
    """
    从追剧列表中移除一个项目。
    返回 True 表示成功，False 表示项目未找到。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM watchlist WHERE item_id = ?", (item_id,))
            conn.commit()
            if cursor.rowcount > 0:
                logger.trace(f"DB: 项目 {item_id} 已从追剧列表移除。")
                return True
            else:
                logger.warning(f"DB: 尝试删除项目 {item_id}，但在追剧列表中未找到。")
                return False
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e).lower():
            logger.error(f"DB: 从追剧列表移除项目时发生数据库锁定错误: {e}", exc_info=True)
        else:
            logger.error(f"DB: 从追剧列表移除项目时发生数据库操作错误: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"DB: 从追剧列表移除项目时发生未知错误: {e}", exc_info=True)
        raise

# 批量强制完结的逻辑
def batch_force_end_watchlist_items(db_path: str, item_ids: List[str]) -> int:
    """
    【V2】批量将追剧项目标记为“强制完结”。
    这会将项目状态设置为 'Ended'，并将 'force_ended' 标志位设为 True。
    这样可以防止常规刷新错误地复活剧集，但允许新一季的检查使其复活。
    返回成功更新的行数。
    """
    if not item_ids:
        return 0
    
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' for _ in item_ids)
            # 将状态更新为 Ended，并设置 force_ended 标志
            sql = f"UPDATE watchlist SET status = 'Completed', force_ended = 1 WHERE item_id IN ({placeholders})"
            
            cursor.execute(sql, item_ids)
            conn.commit()
            
            updated_count = cursor.rowcount
            if updated_count > 0:
                logger.info(f"DB: 批量强制完结了 {updated_count} 个追剧项目。")
            else:
                logger.warning(f"DB: 尝试批量强制完结，但提供的ID在列表中均未找到。")
            return updated_count
    except Exception as e:
        logger.error(f"DB: 批量强制完结追剧项目时发生错误: {e}", exc_info=True)
        raise
# ★★★ 批量更新追剧状态的数据库函数 ★★★
def batch_update_watchlist_status(db_path: str, item_ids: list, new_status: str) -> int:
    """
    批量更新指定项目ID列表的追剧状态。
    
    当状态更新为 'Watching' (例如“重新追剧”) 时，此函数会自动：
    1. 清除暂停日期 (`paused_until`)。
    2. 重置强制完结标志 (`force_ended`)。
    
    Args:
        db_path: 数据库文件路径。
        item_ids: 需要更新的项目ID列表。
        new_status: 要设置的新状态 ('Watching', 'Paused', 'Completed')。
        
    Returns:
        成功更新的行数。
    """
    if not item_ids:
        return 0
        
    try:
        with get_db_connection(db_path) as conn: # 假设你有一个 get_db_connection 函数
            cursor = conn.cursor()
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 准备要更新的字段和值
            updates = {
                "status": new_status,
                "last_checked_at": current_time
            }
            
            # 核心逻辑：如果是“重新追剧”，则需要重置相关状态，让剧集恢复活力
            if new_status == 'Watching':
                updates["paused_until"] = None
                updates["force_ended"] = 0
            
            set_clauses = [f"{key} = ?" for key in updates.keys()]
            values = list(updates.values())
            
            # 使用参数化查询来防止SQL注入，这是处理列表的标准做法
            placeholders = ', '.join(['?'] * len(item_ids))
            sql = f"UPDATE watchlist SET {', '.join(set_clauses)} WHERE item_id IN ({placeholders})"
            
            # 将 item_ids 添加到值列表的末尾以匹配占位符
            values.extend(item_ids)
            
            cursor.execute(sql, tuple(values))
            conn.commit()
            
            logger.info(f"DB: 成功将 {cursor.rowcount} 个项目的状态批量更新为 '{new_status}'。")
            return cursor.rowcount
            
    except Exception as e:
        logger.error(f"批量更新项目状态时数据库出错: {e}", exc_info=True)
        # 重新抛出异常，让上层(API路由)可以捕获并返回500错误
        raise
# ======================================================================
# 模块 5: 电影合集数据访问 (Collections Data Access)
# ======================================================================

def get_all_collections(db_path: str) -> List[Dict[str, Any]]:
    """获取数据库中所有电影合集的信息。"""
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM collections_info WHERE tmdb_collection_id IS NOT NULL ORDER BY name")
            
            final_results = []
            for row in cursor.fetchall():
                row_dict = dict(row)
                # 在数据访问层直接处理 JSON 解析，让上层更省心
                try:
                    row_dict['missing_movies'] = json.loads(row_dict.get('missing_movies_json', '[]'))
                except (json.JSONDecodeError, TypeError):
                    row_dict['missing_movies'] = []
                del row_dict['missing_movies_json'] # 删除原始json字段
                final_results.append(row_dict)
                
            return final_results
    except Exception as e:
        logger.error(f"DB: 读取合集状态时发生严重错误: {e}", exc_info=True)
        raise

def get_all_custom_collection_emby_ids(db_path: str) -> set:
    """
    从 custom_collections 表中获取所有非空的 emby_collection_id。
    返回一个集合(set)以便进行高效的成员资格检查和集合运算。
    """
    ids = set()
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            # 只选择非NULL的ID
            cursor.execute("SELECT emby_collection_id FROM custom_collections WHERE emby_collection_id IS NOT NULL")
            rows = cursor.fetchall()
            for row in rows:
                ids.add(row['emby_collection_id'])
        logger.debug(f"从数据库中获取到 {len(ids)} 个由本程序管理的自定义合集ID。")
        return ids
    except sqlite3.Error as e:
        logger.error(f"获取所有自定义合集Emby ID时发生数据库错误: {e}", exc_info=True)
        return ids # 即使出错也返回一个空集合，保证上层逻辑不会崩溃

def get_collections_with_missing_movies(db_path: str) -> List[Dict[str, Any]]:
    """获取所有包含缺失电影的合集信息。"""
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT emby_collection_id, name, missing_movies_json FROM collections_info WHERE has_missing = 1")
            # 返回原始行，让业务逻辑层处理 JSON
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"DB: 获取有缺失电影的合集时失败: {e}", exc_info=True)
        raise


def update_collection_movies(db_path: str, collection_id: str, movies: List[Dict[str, Any]]):
    """
    更新指定合集的电影列表和缺失状态。
    """
    try:
        with get_db_connection(db_path) as conn:
            # 业务逻辑：根据更新后的电影列表，重新判断是否还有缺失
            still_has_missing = any(m.get('status') == 'missing' for m in movies)
            new_missing_json = json.dumps(movies, ensure_ascii=False)
            
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE collections_info SET missing_movies_json = ?, has_missing = ? WHERE emby_collection_id = ?",
                (new_missing_json, still_has_missing, collection_id)
            )
            conn.commit()
            logger.info(f"DB: 已更新合集 {collection_id} 的电影列表。")
    except Exception as e:
        logger.error(f"DB: 更新合集 {collection_id} 的电影列表时失败: {e}", exc_info=True)
        raise


def update_single_movie_status_in_collection(db_path: str, collection_id: str, movie_tmdb_id: str, new_status: str) -> bool:
    """
    更新合集中单个电影的状态。
    返回 True 表示成功，False 表示合集或电影未找到。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION;")
            
            cursor.execute("SELECT missing_movies_json FROM collections_info WHERE emby_collection_id = ?", (collection_id,))
            row = cursor.fetchone()
            if not row:
                conn.rollback()
                return False # 合集未找到

            try:
                movies = json.loads(row['missing_movies_json'])
            except (json.JSONDecodeError, TypeError):
                movies = []

            movie_found = False
            for movie in movies:
                if str(movie.get('tmdb_id')) == str(movie_tmdb_id):
                    movie['status'] = new_status
                    movie_found = True
                    break
            
            if not movie_found:
                conn.rollback()
                return False # 电影未找到

            # 状态更新后，重新计算合集的 has_missing 标志
            still_has_missing = any(m.get('status') == 'missing' for m in movies)
            new_missing_json = json.dumps(movies, ensure_ascii=False)
            
            cursor.execute(
                "UPDATE collections_info SET missing_movies_json = ?, has_missing = ? WHERE emby_collection_id = ?", 
                (new_missing_json, still_has_missing, collection_id)
            )
            conn.commit()
            logger.info(f"DB: 已更新合集 {collection_id} 中电影 {movie_tmdb_id} 的状态为 '{new_status}'。")
            return True
    except Exception as e:
        logger.error(f"DB: 更新电影状态时发生数据库错误: {e}", exc_info=True)
        if conn and conn.in_transaction:
            conn.rollback()
        raise

# ======================================================================
# 模块 6: 演员订阅数据访问 (Actor Subscriptions Data Access)
# ======================================================================

def get_all_actor_subscriptions(db_path: str) -> List[Dict[str, Any]]:
    """获取所有演员订阅的简略列表。"""
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            # 只选择列表页需要展示的核心字段
            cursor.execute("SELECT id, tmdb_person_id, actor_name, profile_path, status, last_checked_at FROM actor_subscriptions ORDER BY added_at DESC")
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"DB: 获取演员订阅列表失败: {e}", exc_info=True)
        raise


def get_single_subscription_details(db_path: str, subscription_id: int) -> Optional[Dict[str, Any]]:
    """获取单个订阅的完整详情，包括其追踪的所有媒体。"""
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            
            # 1. 获取订阅主信息
            cursor.execute("SELECT * FROM actor_subscriptions WHERE id = ?", (subscription_id,))
            subscription = cursor.fetchone()
            if not subscription:
                return None
            
            # 2. 获取关联的已追踪媒体
            cursor.execute("SELECT * FROM tracked_actor_media WHERE subscription_id = ? ORDER BY release_date DESC", (subscription_id,))
            tracked_media = [dict(row) for row in cursor.fetchall()]
            
            # 3. 组合数据
            response_data = dict(subscription)
            response_data['tracked_media'] = tracked_media
            return response_data
    except Exception as e:
        logger.error(f"DB: 获取订阅详情 {subscription_id} 失败: {e}", exc_info=True)
        raise


def safe_json_dumps(value):
    """
    将 Python 对象转换成 JSON 字符串。
    如果传入的是字符串且能被解析成合法 JSON，则先解析再序列化，避免重复转义。
    否则按字符串处理。
    """
    if isinstance(value, str):
        try:
            # 尝试解析字符串（可能是JSON字符串）
            parsed = json.loads(value)
            # 重新序列化，保证仅一层转义
            return json.dumps(parsed, ensure_ascii=False)
        except Exception:
            # 解析失败，按字符串序列化
            return json.dumps(value, ensure_ascii=False)
    else:
        # 普通Python对象，正常序列化
        return json.dumps(value, ensure_ascii=False)

def add_actor_subscription(db_path: str, tmdb_person_id: int, actor_name: str, profile_path: str, config: dict) -> int:
    """
    新增一个演员订阅。
    正确处理配置中的 JSON 字段，避免多层转义。
    """
    start_year = config.get('start_year', 1900)
    media_types = config.get('media_types', 'Movie,TV')
    genres_include = safe_json_dumps(config.get('genres_include_json', []))
    genres_exclude = safe_json_dumps(config.get('genres_exclude_json', []))
    min_rating = config.get('min_rating', 6.0)

    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO actor_subscriptions 
                (tmdb_person_id, actor_name, profile_path, config_start_year, config_media_types, config_genres_include_json, config_genres_exclude_json, config_min_rating)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tmdb_person_id, actor_name, profile_path, start_year, media_types, genres_include, genres_exclude, min_rating)
            )
            conn.commit()
            new_sub_id = cursor.lastrowid
            logger.info(f"DB: 成功添加演员订阅 '{actor_name}' (ID: {new_sub_id})。")
            return new_sub_id
    except sqlite3.IntegrityError:
        raise
    except Exception as e:
        logger.error(f"DB: 添加演员订阅 '{actor_name}' 时失败: {e}", exc_info=True)
        raise

def update_actor_subscription(db_path: str, subscription_id: int, data: dict) -> bool:
    """
    更新一个演员订阅的状态或配置。
    处理 JSON 字段时避免多层转义。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM actor_subscriptions WHERE id = ?", (subscription_id,))
            current_sub = cursor.fetchone()
            if not current_sub:
                return False

            new_status = data.get('status', current_sub['status'])
            config = data.get('config')

            if config is not None:
                new_start_year = config.get('start_year', current_sub['config_start_year'])
                new_media_types = config.get('media_types', current_sub['config_media_types'])

                # 先拿配置传入值，没有则尝试从数据库旧值解析Python对象
                genres_include_raw = config.get('genres_include_json', current_sub['config_genres_include_json'])
                genres_exclude_raw = config.get('genres_exclude_json', current_sub['config_genres_exclude_json'])

                new_genres_include = safe_json_dumps(genres_include_raw)
                new_genres_exclude = safe_json_dumps(genres_exclude_raw)

                new_min_rating = config.get('min_rating', current_sub['config_min_rating'])
            else:
                new_start_year = current_sub['config_start_year']
                new_media_types = current_sub['config_media_types']
                new_genres_include = current_sub['config_genres_include_json']
                new_genres_exclude = current_sub['config_genres_exclude_json']
                new_min_rating = current_sub['config_min_rating']

            cursor.execute("""
                UPDATE actor_subscriptions SET
                status = ?, config_start_year = ?, config_media_types = ?, 
                config_genres_include_json = ?, config_genres_exclude_json = ?, config_min_rating = ?
                WHERE id = ?
            """, (new_status, new_start_year, new_media_types, new_genres_include, new_genres_exclude, new_min_rating, subscription_id))
            conn.commit()
            logger.info(f"DB: 成功更新订阅ID {subscription_id}。")
            return True
    except Exception as e:
        logger.error(f"DB: 更新订阅 {subscription_id} 失败: {e}", exc_info=True)
        raise


def delete_actor_subscription(db_path: str, subscription_id: int) -> bool:
    """
    删除一个演员订阅及其所有追踪的媒体。
    返回 True 表示成功。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            # 由于外键设置了 ON DELETE CASCADE，我们只需要删除主表记录即可
            cursor.execute("DELETE FROM actor_subscriptions WHERE id = ?", (subscription_id,))
            conn.commit()
            logger.info(f"DB: 成功删除订阅ID {subscription_id}。")
            return True
    except Exception as e:
        logger.error(f"DB: 删除订阅 {subscription_id} 失败: {e}", exc_info=True)
        raise

# ======================================================================
# 模块 6: 自定义电影合集数据访问 (custom_collections Data Access)
# ======================================================================

def create_custom_collection(db_path: str, name: str, type: str, definition_json: str) -> Optional[int]:
    """
    在数据库中创建一个新的自定义合集定义。
    :param name: 合集名称。
    :param type: 合集类型 ('filter' 或 'list')。
    :param definition_json: 存储规则或URL的JSON字符串。
    :return: 新创建的合集的ID，如果失败则返回None。
    """
    sql = """
        INSERT INTO custom_collections (name, type, definition_json, status, created_at)
        VALUES (?, ?, ?, 'active', CURRENT_TIMESTAMP)
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (name, type, definition_json))
            conn.commit()
            logger.info(f"成功创建自定义合集 '{name}' (类型: {type})。")
            return cursor.lastrowid
    except sqlite3.Error as e:
        logger.error(f"创建自定义合集 '{name}' 时发生数据库错误: {e}", exc_info=True)
        return None

def get_all_custom_collections(db_path: str) -> List[Dict[str, Any]]:
    """
    获取所有已定义的自定义合集。
    :return: 包含所有合集信息的字典列表。
    """
    try:
        with get_db_connection(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM custom_collections ORDER BY name ASC")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.error(f"获取所有自定义合集时发生数据库错误: {e}", exc_info=True)
        return []

# ★★★ 获取所有已启用的自定义合集，供“一键生成”任务使用 ★★★
def get_all_active_custom_collections(db_path: str) -> List[Dict[str, Any]]:
    """获取所有状态为 'active' 的自定义合集"""
    try:
        with get_db_connection(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM custom_collections WHERE status = 'active' ORDER BY name ASC")
            rows = cursor.fetchall()
            logger.info(f"从数据库找到 {len(rows)} 个已启用的自定义合集。")
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.error(f"获取所有已启用的自定义合集时发生数据库错误: {e}", exc_info=True)
        return []

def get_custom_collection_by_id(db_path: str, collection_id: int) -> Optional[Dict[str, Any]]:
    """
    根据ID获取单个自定义合集的详细信息。
    :param collection_id: 自定义合集的ID。
    :return: 包含合集信息的字典，如果未找到则返回None。
    """
    try:
        with get_db_connection(db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM custom_collections WHERE id = ?", (collection_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        logger.error(f"根据ID {collection_id} 获取自定义合集时发生数据库错误: {e}", exc_info=True)
        return None

def update_custom_collection(db_path: str, collection_id: int, name: str, type: str, definition_json: str, status: str) -> bool:
    """
    更新一个已存在的自定义合集。
    """
    sql = """
        UPDATE custom_collections
        SET name = ?, type = ?, definition_json = ?, status = ?
        WHERE id = ?
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (name, type, definition_json, status, collection_id))
            conn.commit()
            logger.info(f"成功更新自定义合集 ID: {collection_id}。")
            return True
    except sqlite3.Error as e:
        logger.error(f"更新自定义合集 ID {collection_id} 时发生数据库错误: {e}", exc_info=True)
        return False

def delete_custom_collection(db_path: str, collection_id: int) -> bool:
    """
    【V5 - 职责单一版】从数据库中删除一个自定义合集定义。
    此函数只负责数据库删除操作，不再与任何其他表或外部服务交互。
    联动删除Emby实体的逻辑应由调用方（API层）处理。
    
    :param db_path: 数据库路径。
    :param collection_id: 要删除的自定义合集的数据库ID。
    :return: 如果成功删除了记录，返回 True；如果未找到记录或发生错误，返回 False。
    """
    sql = "DELETE FROM custom_collections WHERE id = ?"
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (collection_id,))
            conn.commit()
            # cursor.rowcount > 0 确保确实有一行被删除了
            if cursor.rowcount > 0:
                logger.info(f"✅ 成功从数据库中删除了自定义合集定义 (ID: {collection_id})。")
                return True
            else:
                logger.warning(f"尝试删除自定义合集 (ID: {collection_id})，但在数据库中未找到该记录。")
                return False # 虽然不是错误，但操作未产生效果
    except sqlite3.Error as e:
        logger.error(f"删除自定义合集 (ID: {collection_id}) 时发生数据库错误: {e}", exc_info=True)
        raise # 向上抛出异常，让API层可以捕获并返回500错误

# +++ 自定义合集筛选引擎所需函数 +++
def get_media_metadata_by_tmdb_id(db_path: str, tmdb_id: str) -> Optional[Dict[str, Any]]:
    """
    根据TMDb ID从媒体元数据缓存表中获取单条记录。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM media_metadata WHERE tmdb_id = ?", (tmdb_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        logger.error(f"根据TMDb ID {tmdb_id} 获取媒体元数据时出错: {e}", exc_info=True)
        return None
    
# ★★★ 新增：获取所有媒体元数据 ★★★
def get_all_media_metadata(db_path: str, item_type: str = 'Movie') -> List[Dict[str, Any]]:
    """
    从媒体元数据缓存表中获取指定类型的所有记录。
    :param item_type: 'Movie' 或 'Series'。默认为 'Movie'，因为合集主要是电影。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM media_metadata WHERE item_type = ?", (item_type,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.error(f"获取所有媒体元数据时出错 (类型: {item_type}): {e}", exc_info=True)
        return []
    
# ★★★ 新增：批量写入媒体元数据 ★★★
def bulk_upsert_media_metadata(db_path: str, metadata_list: List[Dict[str, Any]]):
    """
    使用 INSERT OR REPLACE 批量插入或更新媒体元数据。
    """
    if not metadata_list:
        return

    sql = """
        INSERT OR REPLACE INTO media_metadata (
            tmdb_id, item_type, title, original_title, release_year, rating,
            release_date, date_added, -- ★ 新增
            genres_json, actors_json, directors_json, studios_json, countries_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) -- ★ 占位符数量从11个增加到13个
    """
    
    # 将字典列表转换为元组列表
    data_to_insert = [
        (
            item.get('tmdb_id'), item.get('item_type'), item.get('title'),
            item.get('original_title'), item.get('release_year'), item.get('rating'),
            item.get('release_date'), item.get('date_added'), # ★ 新增
            item.get('genres_json'), item.get('actors_json'), item.get('directors_json'),
            item.get('studios_json'), item.get('countries_json')
        )
        for item in metadata_list
    ]

    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION;")
            cursor.executemany(sql, data_to_insert)
            conn.commit()
            logger.info(f"成功批量写入/更新 {len(data_to_insert)} 条媒体元数据到缓存表。")
    except sqlite3.Error as e:
        logger.error(f"批量写入媒体元数据时发生数据库错误: {e}", exc_info=True)
        # 重新抛出，让上层任务知道失败了
        raise
# ★★★ 从元数据表中提取所有唯一的类型 ★★★
def get_unique_genres(db_path: str) -> List[str]:
    """
    从 media_metadata 表中扫描所有电影，提取出所有不重复的类型(genres)。
    """
    unique_genres = set()
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            # 我们只需要 genres_json 这一列
            cursor.execute("SELECT genres_json FROM media_metadata WHERE item_type = 'Movie'")
            rows = cursor.fetchall()
            
            for row in rows:
                if row['genres_json']:
                    try:
                        # 解析JSON数组
                        genres = json.loads(row['genres_json'])
                        # 将列表中的每个类型都加入到集合中，集合会自动处理重复
                        for genre in genres:
                            if genre: # 确保不是空字符串
                                unique_genres.add(genre.strip())
                    except (json.JSONDecodeError, TypeError):
                        continue # 如果某行数据有问题，跳过
                        
        # 将集合转换为列表并排序
        sorted_genres = sorted(list(unique_genres))
        logger.trace(f"从数据库中成功提取出 {len(sorted_genres)} 个唯一的电影类型。")
        return sorted_genres
        
    except sqlite3.Error as e:
        logger.error(f"提取唯一电影类型时发生数据库错误: {e}", exc_info=True)
        return []

# ★★★ 从元数据表中提取所有唯一的工作室 ★★★
def get_unique_studios(db_path: str) -> List[str]:
    """
    从 media_metadata 表中扫描所有电影，提取出所有不重复的工作室(studios)。
    """
    unique_studios = set()
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT studios_json FROM media_metadata WHERE item_type = 'Movie'")
            rows = cursor.fetchall()
            
            for row in rows:
                if row['studios_json']:
                    try:
                        studios = json.loads(row['studios_json'])
                        for studio in studios:
                            if studio:
                                unique_studios.add(studio.strip())
                    except (json.JSONDecodeError, TypeError):
                        continue
                        
        sorted_studios = sorted(list(unique_studios))
        logger.trace(f"从数据库中成功提取出 {len(sorted_studios)} 个唯一的工作室。")
        return sorted_studios
        
    except sqlite3.Error as e:
        logger.error(f"提取唯一工作室时发生数据库错误: {e}", exc_info=True)
        return []
    
# ★★★ 根据关键词搜索唯一的工作室 ★★★
def search_unique_studios(db_path: str, search_term: str, limit: int = 20) -> List[str]:
    """
    (V3 - 智能排序版)
    从数据库中搜索工作室，并优先返回名称以 search_term 开头的结果。
    """
    if not search_term:
        return []
    
    all_studios = get_unique_studios(db_path)
    
    if not all_studios:
        return []

    search_term_lower = search_term.lower()
    
    # ★★★ 核心升级：创建两个列表来存放不同优先级的匹配结果 ★★★
    starts_with_matches = []
    contains_matches = []
    
    for studio in all_studios:
        studio_lower = studio.lower()
        # 1. 优先检查是否以搜索词开头
        if studio_lower.startswith(search_term_lower):
            starts_with_matches.append(studio)
        # 2. 如果不是开头匹配，再检查是否包含
        elif search_term_lower in studio_lower:
            contains_matches.append(studio)
            
    # ★★★ 核心升级：将两个列表合并，高优先级的在前 ★★★
    final_matches = starts_with_matches + contains_matches
    
    logger.trace(f"智能搜索 '{search_term}'，找到 {len(final_matches)} 个匹配项。")
    
    # 只返回限定数量的结果
    return final_matches[:limit]
# --- 搜索演员 ---
def search_unique_actors(db_path: str, search_term: str, limit: int = 20) -> List[str]:
    """
    (V6 - 中英双语兼容搜索版)
    直接从 media_metadata 表中提取演员的 name 和 original_name 进行搜索。
    用户可以用中文译名或原始外文名进行搜索。
    """
    if not search_term:
        return []
    
    # ★★★ 核心修改 1: 使用字典来存储 unique_name -> original_name 的映射 ★★★
    unique_actors_map = {}
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT actors_json FROM media_metadata")
            rows = cursor.fetchall()
            
            for row in rows:
                if row['actors_json']:
                    try:
                        actors = json.loads(row['actors_json'])
                        for actor in actors:
                            actor_name = actor.get('name')
                            original_name = actor.get('original_name')
                            
                            if actor_name and actor_name.strip():
                                # 使用 actor_name 作为键确保唯一性
                                if actor_name not in unique_actors_map:
                                    unique_actors_map[actor_name.strip()] = (original_name or '').strip()

                    except (json.JSONDecodeError, TypeError):
                        continue
        
        if not unique_actors_map:
            return []

        # 步骤 2: 在提取出的名字集合中进行双语搜索
        search_term_lower = search_term.lower()
        starts_with_matches = []
        contains_matches = []
        
        # ★★★ 核心修改 2: 遍历字典，同时检查 name 和 original_name ★★★
        for name, original_name in sorted(unique_actors_map.items()):
            name_lower = name.lower()
            original_name_lower = original_name.lower()

            # 智能排序：优先匹配开头
            if name_lower.startswith(search_term_lower) or (original_name_lower and original_name_lower.startswith(search_term_lower)):
                starts_with_matches.append(name) # 无论哪个匹配，都返回最终的 name
            # 其次匹配包含
            elif search_term_lower in name_lower or (original_name_lower and search_term_lower in original_name_lower):
                contains_matches.append(name)
        
        final_matches = starts_with_matches + contains_matches
        logger.trace(f"双语搜索演员 '{search_term}'，找到 {len(final_matches)} 个匹配项。")
        
        return final_matches[:limit]
        
    except sqlite3.Error as e:
        logger.error(f"提取并搜索唯一演员时发生数据库错误: {e}", exc_info=True)
        return []
# ★★★ 新增：写入或更新一条完整的合集检查信息 ★★★
def upsert_collection_info(db_path: str, collection_data: Dict[str, Any]):
    """
    使用 INSERT OR REPLACE 写入或更新一条合集信息到 collections_info 表。
    """
    sql = """
        INSERT OR REPLACE INTO collections_info 
        (emby_collection_id, name, tmdb_collection_id, item_type, status, has_missing, 
        missing_movies_json, last_checked_at, poster_path, in_library_count)
        VALUES (:emby_collection_id, :name, :tmdb_collection_id, :item_type, :status, :has_missing, 
        :missing_movies_json, :last_checked_at, :poster_path, :in_library_count)
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, collection_data)
            conn.commit()
            logger.info(f"成功写入/更新合集检查信息到数据库 (ID: {collection_data.get('emby_collection_id')})。")
    except sqlite3.Error as e:
        logger.error(f"写入合集检查信息时发生数据库错误: {e}", exc_info=True)
        raise

def update_custom_collection_after_sync(db_path: str, collection_id: int, update_data: Dict[str, Any]) -> bool:
    """
    在同步任务完成后，使用一个包含多个字段的字典来更新自定义合集的状态。
    这是一个灵活的函数，可以动态构建SQL语句。
    """
    if not update_data:
        logger.warning(f"尝试更新自定义合集 {collection_id}，但没有提供任何更新数据。")
        return False

    # 动态构建 SET 子句
    set_clauses = [f"{key} = ?" for key in update_data.keys()]
    values = list(update_data.values())
    
    sql = f"UPDATE custom_collections SET {', '.join(set_clauses)} WHERE id = ?"
    values.append(collection_id)

    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(values))
            conn.commit()
            logger.trace(f"已更新自定义合集 {collection_id} 的同步后状态。")
            return True
    except sqlite3.Error as e:
        logger.error(f"更新自定义合集 {collection_id} 同步后状态时出错: {e}", exc_info=True)
        return False

def update_single_media_status_in_custom_collection(db_path: str, collection_id: int, media_tmdb_id: str, new_status: str) -> bool:
    """
    更新自定义合集中单个媒体项的状态，并重新计算合集的健康度。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION;")
            
            cursor.execute("SELECT generated_media_info_json FROM custom_collections WHERE id = ?", (collection_id,))
            row = cursor.fetchone()
            if not row:
                conn.rollback()
                return False

            try:
                media_items = json.loads(row['generated_media_info_json'] or '[]')
            except (json.JSONDecodeError, TypeError):
                media_items = []

            item_found = False
            for item in media_items:
                if str(item.get('tmdb_id')) == str(media_tmdb_id):
                    item['status'] = new_status
                    item_found = True
                    break
            
            if not item_found:
                conn.rollback()
                return False

            # 重新计算健康状态
            missing_count = sum(1 for item in media_items if item.get('status') == 'missing')
            new_health_status = 'has_missing' if missing_count > 0 else 'ok'
            
            # 准备更新的数据
            update_data = {
                "generated_media_info_json": json.dumps(media_items, ensure_ascii=False),
                "missing_count": missing_count,
                "health_status": new_health_status
            }
            
            set_clauses = [f"{key} = ?" for key in update_data.keys()]
            values = list(update_data.values())
            sql = f"UPDATE custom_collections SET {', '.join(set_clauses)} WHERE id = ?"
            values.append(collection_id)
            
            cursor.execute(sql, tuple(values))
            conn.commit()
            logger.info(f"DB: 已更新自定义合集 {collection_id} 中媒体 {media_tmdb_id} 的状态为 '{new_status}'。")
            return True
    except Exception as e:
        logger.error(f"DB: 更新自定义合集中媒体状态时发生数据库错误: {e}", exc_info=True)
        if conn and conn.in_transaction:
            conn.rollback()
        raise
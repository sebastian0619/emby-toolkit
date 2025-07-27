# db_handler.py
import sqlite3
import json
from datetime import date, timedelta, datetime
import logging
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)

# ======================================================================
# 模块 1: 数据库管理器 (The Unified Data Access Layer)
# ======================================================================

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
    清空所有待复核项目，并将它们全部标记为已处理。
    返回被成功处理的项目数量。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("BEGIN TRANSACTION;")
            
            # 1. 复制
            copy_sql = """
                REPLACE INTO processed_log (item_id, item_name, processed_at, score)
                SELECT item_id, item_name, CURRENT_TIMESTAMP, COALESCE(score, 10.0)
                FROM failed_log;
            """
            cursor.execute(copy_sql)
            copied_count = cursor.rowcount
            
            # 2. 删除
            cursor.execute("DELETE FROM failed_log")
            deleted_count = cursor.rowcount
            
            # 3. 验证并提交/回滚
            if copied_count == deleted_count:
                conn.commit()
                logger.info(f"DB: 已成功将 {deleted_count} 个项目从待复核列表移至已处理列表。")
                return deleted_count
            else:
                conn.rollback()
                logger.error(f"DB: 清空待复核列表时数据不一致，操作已回滚！(复制: {copied_count}, 删除: {deleted_count})")
                # 抛出一个特定的错误，让上层知道出了问题
                raise RuntimeError("清空待复核列表时发生数据不一致错误。")
                
    except Exception as e:
        logger.error(f"DB: 清空并标记待复核列表时发生未知异常: {e}", exc_info=True)
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
                logger.info(f"DB: 项目 {item_id} 已从追剧列表移除。")
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

# ======================================================================
# 模块 5: 电影合集数据访问 (Collections Data Access)
# ======================================================================

def get_all_collections(db_path: str) -> List[Dict[str, Any]]:
    """获取数据库中所有电影合集的信息。"""
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM collections_info ORDER BY name")
            
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
            new_missing_json = json.dumps(movies)
            
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
            new_missing_json = json.dumps(movies)
            
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


def add_actor_subscription(db_path: str, tmdb_person_id: int, actor_name: str, profile_path: str, config: Dict[str, Any]) -> int:
    """
    新增一个演员订阅。
    如果已存在，会因 UNIQUE 约束而失败，并由上层捕获 IntegrityError。
    成功则返回新订阅的 ID。
    """
    # 从配置字典中安全地提取值
    start_year = config.get('start_year', 1900)
    media_types = config.get('media_types', 'Movie,TV')
    # 确保 genres 是 JSON 字符串
    genres_include = json.dumps(config.get('genres_include_json', []))
    genres_exclude = json.dumps(config.get('genres_exclude_json', []))
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
        # 不记录日志，直接向上抛出，让业务逻辑层知道是“重复”错误
        raise
    except Exception as e:
        logger.error(f"DB: 添加演员订阅 '{actor_name}' 时失败: {e}", exc_info=True)
        raise


def update_actor_subscription(db_path: str, subscription_id: int, data: Dict[str, Any]) -> bool:
    """
    更新一个演员订阅的状态或配置。
    返回 True 表示成功，False 表示未找到。
    """
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            
            # 为了避免覆盖，先获取当前值
            cursor.execute("SELECT * FROM actor_subscriptions WHERE id = ?", (subscription_id,))
            current_sub = cursor.fetchone()
            if not current_sub:
                return False

            # 使用新值，如果新值不存在，则使用当前数据库中的旧值
            new_status = data.get('status', current_sub['status'])
            config = data.get('config')
            
            if config is not None:
                new_start_year = config.get('start_year', current_sub['config_start_year'])
                new_media_types = config.get('media_types', current_sub['config_media_types'])
                new_genres_include = json.dumps(config.get('genres_include_json', json.loads(current_sub['config_genres_include_json'])))
                new_genres_exclude = json.dumps(config.get('genres_exclude_json', json.loads(current_sub['config_genres_exclude_json'])))
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
# core_processor.py
import time
import re
import os
import json
import sqlite3 # 用于数据库操作
from typing import Dict, List, Optional, Any, Tuple
import threading
import local_data_handler
import tmdb_handler
from douban import DoubanApi, clean_character_name_static
# 假设 emby_handler.py, utils.py, logger_setup.py, constants.py 都在同一级别或Python路径中
import emby_handler
import utils # 导入我们上面修改的 utils.py
from logger_setup import logger
import constants

# DoubanApi 的导入和可用性检查
try:
    from douban import DoubanApi # douban.py 现在也使用数据库
    DOUBAN_API_AVAILABLE = True
    logger.info("DoubanApi 模块已成功导入到 core_processor。")
except ImportError:
    logger.error("错误: douban.py 文件未找到或 DoubanApi 类无法导入 (core_processor)。")
    DOUBAN_API_AVAILABLE = False
    # 创建一个假的 DoubanApi 类，以便在 DoubanApi 不可用时程序仍能运行（但功能受限）
    class DoubanApi:
        def __init__(self, *args, **kwargs): logger.warning("使用的是假的 DoubanApi 实例 (core_processor)。")
        def get_acting(self, *args, **kwargs): return {"error": "DoubanApi not available", "cast": []}
        def close(self): pass
        # 添加静态方法以避免 AttributeError，如果 _translate_actor_field 尝试调用它们
        @staticmethod
        def _get_translation_from_db(*args, **kwargs): return None
        @staticmethod
        def _save_translation_to_db(*args, **kwargs): pass


class MediaProcessor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.db_path = config.get('db_path')
        if not self.db_path:
            logger.error("MediaProcessor 初始化失败：未在配置中找到 'db_path'。")
            raise ValueError("数据库路径 (db_path) 未在配置中提供给 MediaProcessor。")

        self.douban_api = None
        # 使用 hasattr 检查常量是否存在，避免 AttributeError
        douban_api_available_flag = getattr(constants, 'DOUBAN_API_AVAILABLE', False)
        if douban_api_available_flag:
            try:
                self.douban_api = DoubanApi(db_path=self.db_path)
                logger.info("DoubanApi 实例已在 MediaProcessor 中创建 (使用数据库缓存)。")
            except Exception as e:
                logger.error(f"MediaProcessor 初始化 DoubanApi 失败: {e}", exc_info=True)
                self.douban_api = DoubanApi(db_path=self.db_path) # 假实例
        else:
            logger.warning("DoubanApi 常量指示不可用或未定义，将使用假的 DoubanApi 实例。")
            self.douban_api = DoubanApi(db_path=self.db_path) # 假实例

        self.emby_url = self.config.get("emby_server_url")
        self.emby_api_key = self.config.get("emby_api_key")
        self.emby_user_id = self.config.get("emby_user_id")
        self.tmdb_api_key = self.config.get("tmdb_api_key", "") # <--- 确保加载
        self.translator_engines = self.config.get("translator_engines_order", constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
        self.data_source_mode = config.get("data_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE)
        self.local_data_path = config.get("local_data_path", constants.DEFAULT_LOCAL_DATA_PATH).strip()
        self.libraries_to_process = config.get("libraries_to_process", [])

        self._stop_event = threading.Event() # 确保 threading 已导入
        self.processed_items_cache = self._load_processed_log_from_db()

        logger.info(f"MediaProcessor 初始化完成。Emby URL: {self.emby_url}, UserID: {self.emby_user_id}")
        logger.info(f"  TMDb API Key: {'已配置' if self.tmdb_api_key else '未配置'}")
        logger.info(f"  数据源处理模式: {self.data_source_mode}")
        logger.info(f"  本地数据源路径: '{self.local_data_path if self.local_data_path else '未配置'}'")
        logger.info(f"  将处理的媒体库ID: {self.libraries_to_process if self.libraries_to_process else '未指定特定库'}")
        logger.info(f"  已从数据库加载 {len(self.processed_items_cache)} 个已处理媒体记录到内存缓存。")
        logger.debug(f"  INIT - self.local_data_path: '{self.local_data_path}'")
        logger.debug(f"  INIT - self.data_source_mode: '{self.data_source_mode}'")
        logger.debug(f"  INIT - self.tmdb_api_key (len): {len(self.tmdb_api_key) if self.tmdb_api_key else 0}")
        logger.debug(f"  INIT - DOUBAN_API_AVAILABLE (from top level): {DOUBAN_API_AVAILABLE}") # 打印顶层导入状态
        logger.debug(f"  INIT - self.douban_api is None: {self.douban_api is None}")
        if self.douban_api:
            logger.debug(f"  INIT - self.douban_api type: {type(self.douban_api)}")


    def _get_db_connection(self) -> sqlite3.Connection:
        """获取数据库连接的辅助方法"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row # 方便按列名访问
        return conn

    def signal_stop(self):
        logger.info("MediaProcessor 收到停止信号。")
        self._stop_event.set()

    def clear_stop_signal(self):
        self._stop_event.clear()
        logger.debug("MediaProcessor 停止信号已清除。")

    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def _load_processed_log_from_db(self) -> set:
        log_set = set()
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT item_id FROM processed_log")
            rows = cursor.fetchall()
            for row in rows:
                log_set.add(row['item_id'])
            conn.close()
        except Exception as e:
            logger.error(f"从数据库读取已处理记录失败: {e}", exc_info=True)
        return log_set

    def save_to_processed_log(self, item_id: str, item_name: Optional[str] = None, score: Optional[float] = None):
        """将成功处理的媒体项ID、名称和评分保存到SQLite数据库和内存缓存中。"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "REPLACE INTO processed_log (item_id, item_name, processed_at, score) VALUES (?, ?, CURRENT_TIMESTAMP, ?)",
                (item_id, item_name if item_name else f"未知项目(ID:{item_id})", score)
            )
            conn.commit()
            conn.close()
            
            # 先准备好评分的显示字符串
            score_display = f"{score:.1f}" if score is not None else "N/A" # <--- 计算好显示字符串

            if item_id not in self.processed_items_cache:
                self.processed_items_cache.add(item_id)
                logger.info(f"Item ID '{item_id}' ('{item_name}') 已添加到已处理记录 (数据库[评分:{score_display}]和内存)。") # <--- 使用 score_display
            else:
                # 如果 logger.debug 这行也需要显示 score，同样处理
                logger.debug(f"Item ID '{item_id}' ('{item_name}') 已更新/确认在已处理记录 (数据库[评分:{score_display}])。") # <--- 使用 score_display
        except Exception as e:
            logger.error(f"保存已处理记录到数据库失败 (Item ID: {item_id}): {e}", exc_info=True)

    def clear_processed_log(self):
        """清除数据库中的已处理记录和内存缓存。"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM processed_log")
            conn.commit()
            conn.close()
            self.processed_items_cache.clear()
            logger.info("数据库和内存中的已处理记录已清除。")
        except Exception as e:
            logger.error(f"清除数据库已处理记录失败: {e}", exc_info=True)

    def save_to_failed_log(self, item_id: str, item_name: Optional[str], error_msg: str, item_type: Optional[str] = None, score: Optional[float] = None): # <--- 1. 添加 score 参数
        """将处理失败的媒体项信息和评分保存到SQLite数据库。"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()

            # 2. 修改 SQL 语句以包含 score 列
            # 使用 REPLACE INTO 来确保如果 item_id 已存在，则更新记录
            cursor.execute(
                "REPLACE INTO failed_log (item_id, item_name, failed_at, error_message, item_type, score) VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?)",
                (item_id, item_name if item_name else f"未知项目(ID:{item_id})", error_msg, item_type if item_type else "未知类型", score) # <--- 3. 传递 score 给 SQL
            )
            conn.commit()
            conn.close()
            
            # 4. 更新日志消息以包含分数
            score_info = f"(评分为: {score:.1f})" if score is not None else "(评分未记录/不适用)"
            logger.info(f"Item ID '{item_id}' ('{item_name}') 已作为失败/待手动处理项记录到数据库。原因: {error_msg} {score_info}")
        except Exception as e:
            logger.error(f"保存失败记录到数据库失败 (Item ID: {item_id}): {e}", exc_info=True)

    def _translate_actor_field(self, text: Optional[str], field_name: str, 
                               actor_name_for_log: str, 
                               db_cursor_for_cache: sqlite3.Cursor) -> Optional[str]: # 添加 db_cursor_for_cache 参数
        """
        翻译演员的特定字段（名字或角色名），使用缓存和在线翻译引擎。
        使用传入的数据库游标 db_cursor_for_cache 来保存新的翻译缓存。
        """
        if self.is_stop_requested():
            logger.debug(f"翻译字段 '{field_name}' for '{actor_name_for_log}' 前检测到停止信号，跳过。")
            return text # 返回原文，因为任务已停止

        if not text or not text.strip():
            # logger.debug(f"字段 '{field_name}' for '{actor_name_for_log}' 为空或全空白，无需翻译。")
            return text # 如果原文就是空或空白，直接返回

        # 检查是否已包含中文，如果是，则不翻译 (utils.contains_chinese 需要可用)
        if utils.contains_chinese(text):
            # logger.debug(f"字段 '{field_name}' ('{text}') for '{actor_name_for_log}' 已包含中文，跳过翻译。")
            return text

        text_stripped = text.strip()
        # 跳过单字母或双大写字母的逻辑 (保持你原有的逻辑)
        if len(text_stripped) == 1 and 'A' <= text_stripped.upper() <= 'Z':
            logger.debug(f"字段 '{field_name}' ('{text_stripped}') for '{actor_name_for_log}' 为单字母，跳过翻译。")
            return text
        if len(text_stripped) == 2 and text_stripped.isupper() and text_stripped.isalpha():
            logger.debug(f"字段 '{field_name}' ('{text_stripped}') for '{actor_name_for_log}' 为双大写字母，跳过翻译。")
            return text

        # 1. 从数据库读取翻译缓存
        # DoubanApi._get_translation_from_db 是类方法，它自己管理连接进行读取，这通常没问题，因为读操作锁级别较低
        cached_entry = None
        if self.douban_api and hasattr(DoubanApi, '_get_translation_from_db'):
             cached_entry = DoubanApi._get_translation_from_db(text_stripped) 

        if cached_entry:
            cached_translation = cached_entry.get("translated_text")
            engine_used = cached_entry.get("engine_used")
            if cached_translation and cached_translation.strip(): # 缓存命中且有有效翻译
                logger.info(f"数据库翻译缓存命中 for '{text_stripped}' -> '{cached_translation}' (引擎: {engine_used}, 演员: {actor_name_for_log}, 字段: {field_name})")
                return cached_translation
            else: # 缓存中存的是 None 或空字符串，表示之前翻译失败或无结果
                logger.info(f"数据库翻译缓存命中 (空值/之前失败) for '{text_stripped}' (演员: {actor_name_for_log}, 字段: {field_name})，将重新尝试在线翻译。")
                # 不返回 text，让流程继续到在线翻译

        # 2. 如果缓存未命中或缓存的是失败记录，则进行在线翻译
        logger.info(f"准备在线翻译字段 '{field_name}': '{text_stripped}' (演员: {actor_name_for_log})")
        translation_result = utils.translate_text_with_translators( # 调用 utils 中的翻译函数
            text_stripped,
            engine_order=self.translator_engines # self.translator_engines 来自配置
        ) 
        logger.debug(f"在线翻译API调用后，translation_result for '{text_stripped}': {translation_result}")
        
        translated_text_online: Optional[str] = None
        used_engine_online: str = "unknown"
        if translation_result and isinstance(translation_result, dict):
            translated_text_online = translation_result.get("text")
            used_engine_online = translation_result.get("engine", "unknown")

        if translated_text_online and translated_text_online.strip():
            final_translation = translated_text_online.strip()
            logger.info(f"在线翻译成功 ({used_engine_online}): '{text_stripped}' -> '{final_translation}' (演员: {actor_name_for_log}, 字段: {field_name})")
            
            # 3. 保存新的翻译结果到数据库缓存，使用传入的游标
            if self.douban_api and hasattr(DoubanApi, '_save_translation_to_db'):
                DoubanApi._save_translation_to_db(
                    text_stripped, 
                    final_translation, 
                    used_engine_online, 
                    cursor=db_cursor_for_cache # <--- 传递游标
                )
            return final_translation
        else:
            logger.warning(f"在线翻译失败或返回空 for '{text_stripped}' (演员: {actor_name_for_log}, 字段: {field_name}, 尝试引擎: {used_engine_online})")
            
            # 3. 保存翻译失败的状态 (None) 到数据库缓存，使用传入的游标
            if self.douban_api and hasattr(DoubanApi, '_save_translation_to_db'):
                DoubanApi._save_translation_to_db(
                    text_stripped, 
                    None, # 保存 None 表示翻译失败
                    f"failed_or_empty_via_{used_engine_online}", 
                    cursor=db_cursor_for_cache # <--- 传递游标
                )
            return text # 在线翻译失败，返回原文

    def _evaluate_cast_processing_quality(self, final_cast: List[Dict[str, Any]], original_emby_cast_count: int) -> float:
        """
        评估处理后的演员列表质量，并返回一个分数 (0.0 - 10.0)。
        这是一个初始的、相对简化的打分版本。
        """
        logger.debug(f"  质量评估开始：原始演员数={original_emby_cast_count}, 处理后演员数={len(final_cast)}")

        # 情况1: 原始就没有演员
        if original_emby_cast_count == 0:
            if not final_cast: # 处理后也没有，这是正常的
                logger.debug("  质量评估：原始无演员，处理后也无演员。评为 10.0 分 (无需处理)。")
                return 10.0
            else: # 原本没有，但处理后反而有了演员（这不符合我们“不新增”的原则，但打分逻辑先不管这个）
                logger.warning("  质量评估：原始无演员，但处理后新增了演员。这种情况的评分需要根据业务逻辑定义。暂时评为 5.0 分。")
                return 5.0 # 或者根据你的业务逻辑给一个合适的分数

        # 情况2: 原本有演员，但处理后演员列表为空
        if not final_cast and original_emby_cast_count > 0:
            logger.warning(f"  质量评估：原始有 {original_emby_cast_count} 位演员，但处理后演员列表为空！评为 0.0 分。")
            return 0.0

        # 情况3: 原本有演员，处理后也有演员，开始逐个评估
        total_actors_in_final_list = len(final_cast)
        accumulated_score = 0.0

        for actor_data in final_cast:
            actor_score = 0.0 # 每个演员从0分开始累加

            # --- 演员名评分 (满分 3 分) ---
            name = actor_data.get("Name", "")
            if name and utils.contains_chinese(name):
                actor_score += 2.0 # 有中文名基础分
                # 假设 _source_comment 可以告诉我们名字来源
                source_comment = actor_data.get("_source_comment", "")
                if "douban" in source_comment.lower() and "translated" not in source_comment.lower():
                    actor_score += 1.0 # 来自豆瓣的非翻译中文名，再加1分
                elif "translated" in source_comment.lower():
                    actor_score += 0.0 # 如果是翻译的，不多加分（基础的2分已包含）
                else: # 其他情况（比如Emby原始中文名）
                    actor_score += 0.5
            elif name: # 有名字但不是中文
                actor_score += 0.0 # 非中文名不得分
            else: # 没有名字
                actor_score -= 1.0 # 扣分

            # --- 角色名评分 (满分 3 分) ---
            role = actor_data.get("Role", "")
            if role and utils.contains_chinese(role):
                # 假设角色名已经是通过 utils.clean_character_name_static 清理过的
                actor_score += 2.0 # 有中文角色名基础分
                source_comment = actor_data.get("_source_comment", "") # 复用上面的source_comment
                if "douban" in source_comment.lower() and "translated" not in source_comment.lower():
                    actor_score += 1.0 # 来自豆瓣的非翻译中文角色名
                elif "emby_original_cleaned" in source_comment and "translated" not in source_comment.lower():
                    actor_score += 0.8 # Emby原始但已清理的中文角色名
                elif "translated" in source_comment.lower():
                    actor_score += 0.0
                else:
                    actor_score += 0.5
            elif role: # 有角色名但不是中文
                actor_score += 0.0
            # 如果是演员类型但没有角色名，可以考虑轻微扣分，但这里简化，不扣

            # --- Provider ID 评分 (满分 4 分) ---
            # EmbyPersonId 是必须的（因为我们不新增），所以不单独为它加分，但如果缺失则前面已过滤
            if actor_data.get("DoubanCelebrityId"):
                actor_score += 1.5 # 豆瓣ID比较重要
            if actor_data.get("TmdbPersonId"):
                actor_score += 1.0
            if actor_data.get("ImdbId"):
                actor_score += 1.5 # IMDb ID 也比较重要

            # 确保单个演员分数在 0 到 10 之间
            final_actor_score = max(0.0, min(10.0, actor_score))
            accumulated_score += final_actor_score
            logger.debug(f"    演员 '{actor_data.get('Name', '未知')}' (角色: '{actor_data.get('Role', '无')}') 单项评分: {final_actor_score:.1f}")

        # 计算最终媒体项的平均分
        average_media_score = accumulated_score / total_actors_in_final_list if total_actors_in_final_list > 0 else 0.0
        
        # 可以根据演员数量变化进行调整 (可选)
        # 例如，如果演员数量减少超过一定比例（非合理去重导致），则降低总分
        # if total_actors_in_final_list < original_emby_cast_count * 0.7: # 例如，如果演员少了30%以上
        #     logger.warning(f"  质量评估：演员数量从 {original_emby_cast_count} 减少到 {total_actors_in_final_list}，可能存在问题。")
        #     average_media_score *= 0.8 # 惩罚性降低总分

        final_score_rounded = round(average_media_score, 1)
        logger.info(f"  媒体项演员处理质量评估完成，最终评分: {final_score_rounded:.1f} (基于 {total_actors_in_final_list} 位演员的平均分)")
        return final_score_rounded
    
    def _update_person_map_entry_in_processor(self, cursor: sqlite3.Cursor, 
                                              emby_pid: str, 
                                              emby_name: str, 
                                              tmdb_id: Optional[str] = None, 
                                              tmdb_name_override: Optional[str] = None,
                                              douban_id: Optional[str] = None, 
                                              douban_name_override: Optional[str] = None,
                                              imdb_id: Optional[str] = None) -> bool:
        """
        辅助函数：在 MediaProcessor 内部更新或插入单条 person_identity_map 记录。
        返回 True 如果操作影响了行，否则 False。
        """
        if not emby_pid:
            logger.warning("MediaProcessor._update_person_map_entry: emby_pid 为空，无法更新映射表。")
            return False

        # 准备名字字段
        final_tmdb_name = tmdb_name_override if tmdb_name_override is not None else (emby_name if tmdb_id else None)
        final_douban_name = douban_name_override if douban_name_override is not None else (emby_name if douban_id else None)

        sql_upsert = """
            INSERT INTO person_identity_map (
                emby_person_id, emby_person_name, 
                imdb_id, tmdb_person_id, douban_celebrity_id, 
                tmdb_name, douban_name, 
                last_updated_at, last_synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(emby_person_id) DO UPDATE SET
                emby_person_name = COALESCE(excluded.emby_person_name, person_identity_map.emby_person_name),
                imdb_id = COALESCE(excluded.imdb_id, person_identity_map.imdb_id),
                tmdb_person_id = COALESCE(excluded.tmdb_person_id, person_identity_map.tmdb_person_id),
                douban_celebrity_id = COALESCE(excluded.douban_celebrity_id, person_identity_map.douban_celebrity_id),
                tmdb_name = COALESCE(excluded.tmdb_name, person_identity_map.tmdb_name),
                douban_name = COALESCE(excluded.douban_name, person_identity_map.douban_name),
                last_updated_at = CURRENT_TIMESTAMP,
                last_synced_at = CURRENT_TIMESTAMP; 
        """
        params = (
            emby_pid, emby_name,
            imdb_id, tmdb_id, douban_id,
            final_tmdb_name, final_douban_name
        )

        try:
            logger.debug(f"    MediaProcessor: Executing UPSERT for EmbyPID {emby_pid} with PARAMS: {params}")
            cursor.execute(sql_upsert, params)
            if cursor.rowcount > 0:
                logger.debug(f"    MediaProcessor: EmbyPID {emby_pid} UPSERTED/UPDATED in person_identity_map. Rowcount: {cursor.rowcount}")
                return True
            else:
                logger.debug(f"    MediaProcessor: EmbyPID {emby_pid} UPSERT executed but rowcount is 0 (no change or no operation).")
                return False 
        except sqlite3.Error as e_upsert:
            logger.error(f"    MediaProcessor: UPSERT for EmbyPID '{emby_pid}' in person_identity_map 失败: {e_upsert}", exc_info=True)
            return False
        
    def _select_best_role(self, current_role: str, candidate_role: str) -> str:
        """
        根据优先级选择最佳角色名。
        优先级: 有内容的候选角色名 > 现有角色名 > '演员' > 空字符串
        """
        current_role = str(current_role or '').strip()
        candidate_role = str(candidate_role or '').strip()

        if candidate_role and candidate_role != "演员":
            return candidate_role
        if not candidate_role and current_role:
            return current_role
        return current_role
    
    def _fetch_douban_cast(self, media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从豆瓣API获取演员原始数据。"""
        # 假设 constants 和 self.douban_api 已经存在
        if not (getattr(constants, 'DOUBAN_API_AVAILABLE', False) and self.douban_api and \
                self.data_source_mode in [constants.DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE, constants.DOMESTIC_SOURCE_MODE_ONLINE_ONLY]):
            return []
        
        logger.debug("调用豆瓣 API get_acting...")
        douban_data = self.douban_api.get_acting(
            name=media_info.get("Name"),
            imdbid=media_info.get("ProviderIds", {}).get("Imdb"),
            mtype="movie" if media_info.get("Type") == "Movie" else ("tv" if media_info.get("Type") == "Series" else None),
            year=str(media_info.get("ProductionYear", "")),
            douban_id_override=media_info.get("ProviderIds", {}).get("Douban")
        )
        if douban_data and not douban_data.get("error") and isinstance(douban_data.get("cast"), list):
            return douban_data["cast"]
        return []
    
    def _format_douban_cast(self, douban_api_actors_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """格式化豆瓣原始演员数据并进行初步去重。"""
        formatted_candidates = []
        seen_douban_ids = set()
        seen_name_sigs = set()
        for item in douban_api_actors_raw:
            douban_id = str(item.get("id", "")).strip() or None
            name_zh = str(item.get("name", "")).strip()
            if not name_zh: continue

            # 基于ID或名字进行初步去重
            if douban_id:
                if douban_id in seen_douban_ids: continue
                seen_douban_ids.add(douban_id)
            else:
                name_sig = f"{name_zh.lower()}|{str(item.get('original_name', '')).lower().strip()}"
                if name_sig in seen_name_sigs: continue
                seen_name_sigs.add(name_sig)
            
            formatted_candidates.append({
                "Name": name_zh,
                "OriginalName": str(item.get("original_name", "")).strip(),
                "Role": str(item.get("character", "")).strip(),
                "DoubanCelebrityId": douban_id,
                "ProviderIds": {"Douban": douban_id} if douban_id else {},
            })
        return formatted_candidates
    
    def _fetch_external_ids_for_person(self, person_candidate: Dict[str, Any], media_info: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """为给定的候选人查询TMDb和IMDb ID。"""
        # 假设 utils 和 tmdb_handler 已经导入
        name_zh = person_candidate.get("Name")
        name_orig = person_candidate.get("OriginalName")
        
        search_query = name_zh
        if not utils.contains_chinese(str(search_query or "")):
            if name_orig: search_query = name_orig
        elif not search_query and name_orig:
            search_query = name_orig

        if not search_query or not search_query.strip():
            logger.debug(f"  跳过TMDb搜索，候选人 '{name_zh}' 无有效搜索词。")
            return None, None, None

        logger.debug(f"  -> 正在为 '{search_query}' (源自 '{name_zh}') 搜索TMDb...")
        tmdb_results = tmdb_handler.search_person_tmdb(search_query, self.tmdb_api_key)
        
        media_year = int(media_info.get("ProductionYear")) if str(media_info.get("ProductionYear", "")).isdigit() else None
        
        selected_person = tmdb_handler.select_best_person_match(
            search_query, tmdb_results,
            target_media_year=media_year,
            known_for_titles=[media_info.get("Name")] if media_info.get("Name") else None
        )

        if selected_person and selected_person.get("id"):
            tmdb_id = str(selected_person.get("id"))
            tmdb_name = selected_person.get("name")
            logger.debug(f"    TMDb匹配成功: '{tmdb_name}' (ID: {tmdb_id})")
            details = tmdb_handler.get_person_details_tmdb(int(tmdb_id), self.tmdb_api_key, append_to_response="external_ids")
            if details and details.get("external_ids", {}).get("imdb_id"):
                imdb_id = details["external_ids"]["imdb_id"]
                logger.debug(f"      获取到 IMDb ID: {imdb_id}")
                return tmdb_id, imdb_id, tmdb_name
            return tmdb_id, None, tmdb_name
        
        logger.debug(f"    TMDb未能为 '{search_query}' 找到匹配。")
        return None, None, None

    def _process_cast_list(self, current_emby_cast_people: List[Dict[str, Any]], media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        media_name_for_log = media_info.get("Name", "未知媒体")
        media_id_for_log = media_info.get("Id", "未知ID")
        media_type_for_log = media_info.get("Type")
        logger.info(f"开始处理媒体 '{media_name_for_log}' (ID: {media_id_for_log}) 的演员列表，遵循 'enrich-only' 模式。")

        conn: Optional[sqlite3.Connection] = None
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()

            # ======================================================================
            # 步骤 1: 初始化 Emby 演员基准列表
            # ======================================================================
            logger.info(f"步骤 1: 初始化 Emby 演员基准列表 ({len(current_emby_cast_people)} 位)...")
            final_cast_list: List[Dict[str, Any]] = []
            for person_emby in current_emby_cast_people:
                if self.is_stop_requested(): break
                emby_pid = str(person_emby.get("Id", "")).strip()
                emby_name = str(person_emby.get("Name", "")).strip()
                if not emby_pid or not emby_name:
                    continue

                provider_ids = person_emby.get("ProviderIds", {})
                actor_internal_format = {
                    "Name": emby_name,
                    "OriginalName": person_emby.get("OriginalName", emby_name),
                    "Role": str(person_emby.get("Role", "")).strip(),
                    "Type": "Actor",
                    "EmbyPersonId": emby_pid,
                    "TmdbPersonId": str(provider_ids.get("Tmdb", "")).strip() or None,
                    "DoubanCelebrityId": str(provider_ids.get("Douban", "")).strip() or None,
                    "ImdbId": str(provider_ids.get("Imdb", "")).strip() or None,
                    "ProviderIds": provider_ids.copy(),
                    "_source_comment": "from_emby_initial"
                }
                final_cast_list.append(actor_internal_format)

                # 使用 Emby 的原始数据更新映射表
                self._update_person_map_entry_in_processor(
                    cursor,
                    emby_pid=actor_internal_format["EmbyPersonId"],
                    emby_name=actor_internal_format["Name"],
                    tmdb_id=actor_internal_format["TmdbPersonId"],
                    douban_id=actor_internal_format["DoubanCelebrityId"],
                    imdb_id=actor_internal_format["ImdbId"]
                )
            logger.info(f"步骤 1: 基准列表创建完成，包含 {len(final_cast_list)} 位演员。")
            if self.is_stop_requested():
                conn.commit()
                return final_cast_list

            # ======================================================================
            # 步骤 2: 从豆瓣获取数据并与基准列表比对
            # ======================================================================
            logger.info("步骤 2: 获取豆瓣演员并与基准列表比对...")
            formatted_douban_candidates = []
            if media_type_for_log in ["Movie", "Series"]:
                logger.info(f"步骤 2: 获取豆瓣演员并与基准列表比对 (因为类型是 {media_type_for_log})...")
                douban_api_actors_raw = self._fetch_douban_cast(media_info)
                formatted_douban_candidates = self._format_douban_cast(douban_api_actors_raw)
                logger.info(f"从豆瓣 API 格式化后得到 {len(formatted_douban_candidates)} 位候选演员。")
            else:
                logger.info(f"步骤 2: 跳过获取豆瓣演员 (因为类型是 {media_type_for_log})。")

            unmatched_douban_candidates: List[Dict[str, Any]] = []
            if formatted_douban_candidates:
                matched_douban_indices = set()
                for i, douban_candidate in enumerate(formatted_douban_candidates):
                    if self.is_stop_requested(): break
                    
                    match_found = False
                    for emby_actor_to_update in final_cast_list:
                        is_match, match_reason = False, ""
                        dc_douban_id = douban_candidate.get("DoubanCelebrityId")
                        if dc_douban_id and dc_douban_id == emby_actor_to_update.get("DoubanCelebrityId"):
                            is_match, match_reason = True, f"Douban ID ({dc_douban_id})"
                        
                        if not is_match:
                            dc_name_lower = douban_candidate.get("Name", "").lower().strip()
                            dc_orig_name_lower = douban_candidate.get("OriginalName", "").lower().strip()
                            emby_name_lower = emby_actor_to_update.get("Name", "").lower().strip()
                            emby_orig_name_lower = emby_actor_to_update.get("OriginalName", "").lower().strip()
                            if dc_name_lower and (dc_name_lower == emby_name_lower or dc_name_lower == emby_orig_name_lower):
                                is_match, match_reason = True, f"名字匹配 (豆瓣中文名: '{douban_candidate.get('Name')}')"
                            elif dc_orig_name_lower and (dc_orig_name_lower == emby_name_lower or dc_orig_name_lower == emby_orig_name_lower):
                                is_match, match_reason = True, f"名字匹配 (豆瓣外文名: '{douban_candidate.get('OriginalName')}')"

                        if is_match:
                            logger.info(f"  匹配成功: 豆瓣候选 '{douban_candidate.get('Name')}' 通过 [{match_reason}] 关联到 Emby 演员 '{emby_actor_to_update.get('Name')}' (PID: {emby_actor_to_update.get('EmbyPersonId')})")
                            
                            if dc_douban_id and not emby_actor_to_update.get("DoubanCelebrityId"):
                                emby_actor_to_update["DoubanCelebrityId"] = dc_douban_id
                                emby_actor_to_update["ProviderIds"]["Douban"] = dc_douban_id
                                logger.info(f"    -> 已为该演员补充 Douban ID: {dc_douban_id}")

                            original_role = emby_actor_to_update.get("Role")
                            candidate_role = utils.clean_character_name_static(douban_candidate.get("Role"))
                            best_role = self._select_best_role(original_role, candidate_role)
                            if best_role != original_role:
                                emby_actor_to_update["Role"] = best_role
                                logger.info(f"    -> 角色名已更新: '{original_role}' -> '{best_role}'")
                            
                            self._update_person_map_entry_in_processor(
                                cursor,
                                emby_pid=emby_actor_to_update["EmbyPersonId"],
                                emby_name=emby_actor_to_update["Name"],
                                douban_id=emby_actor_to_update.get("DoubanCelebrityId"),
                                douban_name_override=douban_candidate.get("Name")
                            )
                            
                            matched_douban_indices.add(i)
                            match_found = True
                            break
                    
                    if not match_found:
                        unmatched_douban_candidates.append(douban_candidate)

            logger.info(f"步骤 2: 完成。{len(matched_douban_indices) if formatted_douban_candidates else 0} 位豆瓣演员已匹配，{len(unmatched_douban_candidates)} 位溢出进入下一步。")
            if self.is_stop_requested():
                conn.commit()
                return final_cast_list

            # ======================================================================
            # 步骤 3: 处理溢出的豆瓣演员
            # ======================================================================
            logger.info(f"步骤 3: 处理 {len(unmatched_douban_candidates)} 位溢出的豆瓣演员 (TMDb/IMDb 交叉匹配)...")
            if self.tmdb_api_key and unmatched_douban_candidates:
                for douban_candidate in unmatched_douban_candidates:
                    if self.is_stop_requested(): break
                    
                    tmdb_id, imdb_id, tmdb_name = self._fetch_external_ids_for_person(douban_candidate, media_info)
                    
                    match_found = False
                    if tmdb_id or imdb_id:
                        for emby_actor_to_update in final_cast_list:
                            is_match, match_reason = False, ""
                            if imdb_id and imdb_id == emby_actor_to_update.get("ImdbId"):
                                is_match, match_reason = True, f"IMDb ID ({imdb_id})"
                            elif tmdb_id and tmdb_id == emby_actor_to_update.get("TmdbPersonId"):
                                is_match, match_reason = True, f"TMDb ID ({tmdb_id})"

                            if is_match:
                                logger.info(f"  交叉匹配成功: 豆瓣候选 '{douban_candidate.get('Name')}' 通过 [{match_reason}] 关联到 Emby 演员 '{emby_actor_to_update.get('Name')}' (PID: {emby_actor_to_update.get('EmbyPersonId')})")
                                
                                updated_ids = {}
                                if douban_candidate.get("DoubanCelebrityId") and not emby_actor_to_update.get("DoubanCelebrityId"):
                                    emby_actor_to_update["DoubanCelebrityId"] = douban_candidate["DoubanCelebrityId"]
                                    emby_actor_to_update["ProviderIds"]["Douban"] = douban_candidate["DoubanCelebrityId"]
                                    updated_ids['Douban'] = douban_candidate["DoubanCelebrityId"]
                                if tmdb_id and not emby_actor_to_update.get("TmdbPersonId"):
                                    emby_actor_to_update["TmdbPersonId"] = tmdb_id
                                    emby_actor_to_update["ProviderIds"]["Tmdb"] = tmdb_id
                                    updated_ids['TMDb'] = tmdb_id
                                if imdb_id and not emby_actor_to_update.get("ImdbId"):
                                    emby_actor_to_update["ImdbId"] = imdb_id
                                    emby_actor_to_update["ProviderIds"]["Imdb"] = imdb_id
                                    updated_ids['IMDb'] = imdb_id
                                if updated_ids: logger.info(f"    -> 已为该演员补充 IDs: {updated_ids}")

                                original_role = emby_actor_to_update.get("Role")
                                candidate_role = utils.clean_character_name_static(douban_candidate.get("Role"))
                                best_role = self._select_best_role(original_role, candidate_role)
                                if best_role != original_role:
                                    emby_actor_to_update["Role"] = best_role
                                    logger.info(f"    -> 角色名已更新: '{original_role}' -> '{best_role}'")

                                self._update_person_map_entry_in_processor(
                                    cursor,
                                    emby_pid=emby_actor_to_update["EmbyPersonId"],
                                    emby_name=emby_actor_to_update["Name"],
                                    douban_id=emby_actor_to_update.get("DoubanCelebrityId"),
                                    tmdb_id=emby_actor_to_update.get("TmdbPersonId"),
                                    imdb_id=emby_actor_to_update.get("ImdbId"),
                                    douban_name_override=douban_candidate.get("Name"),
                                    tmdb_name_override=tmdb_name
                                )
                                
                                match_found = True
                                break
                    
                    if not match_found:
                        logger.info(f"  丢弃: 豆瓣候选 '{douban_candidate.get('Name')}' (D:{douban_candidate.get('DoubanCelebrityId')}, T:{tmdb_id}, I:{imdb_id}) 未能匹配任何基准演员，将被丢弃。")

            logger.info("步骤 3: 完成。")
            if self.is_stop_requested():
                conn.commit()
                return final_cast_list

            # ======================================================================
            # 步骤 4: 对最终演员表进行翻译和格式化
            # ======================================================================
            logger.info(f"步骤 4: 对最终 {len(final_cast_list)} 位演员进行翻译和格式化...")
            is_animation = "Animation" in media_info.get("Genres", []) or "动画" in media_info.get("Genres", [])
            
            for actor_data in final_cast_list:
                if self.is_stop_requested(): break
                
                current_name = actor_data.get("Name")
                if current_name and not utils.contains_chinese(current_name):
                    translated_name = self._translate_actor_field(current_name, "演员名", current_name, db_cursor_for_cache=cursor)
                    if translated_name and current_name != translated_name:
                        actor_data["Name"] = translated_name

                role_cleaned = utils.clean_character_name_static(actor_data.get("Role"))
                final_role = role_cleaned
                if role_cleaned and not utils.contains_chinese(role_cleaned):
                    translated_role = self._translate_actor_field(role_cleaned, "角色名", actor_data.get("Name"), db_cursor_for_cache=cursor)
                    if translated_role and translated_role.strip() and translated_role.strip() != role_cleaned:
                        final_role = translated_role.strip()
                
                if is_animation:
                    final_role = f"{final_role} (配音)" if final_role and not final_role.endswith("(配音)") else "配音"
                elif not final_role:
                    final_role = "演员"
                
                if final_role != actor_data.get("Role"):
                    logger.debug(f"  角色名格式化: '{actor_data.get('Role')}' -> '{final_role}' (演员: {actor_data.get('Name')})")
                    actor_data["Role"] = final_role

            logger.info("步骤 4: 完成。")
            
            logger.info(f"处理完影片 '{media_name_for_log}' 的所有演员，提交数据库更改...")
            conn.commit()
            logger.info("数据库更改已提交。")

            logger.info(f"演员列表最终处理完成，返回 {len(final_cast_list)} 位演员。")
            return final_cast_list

        except Exception as e:
            logger.error(f"处理演员列表时发生严重错误 for media '{media_name_for_log}': {e}", exc_info=True)
            if conn:
                try: conn.rollback()
                except Exception as rb_err: logger.error(f"数据库回滚失败: {rb_err}")
            return []
        finally:
            if conn:
                try: conn.close()
                except Exception as close_err: logger.error(f"数据库连接关闭失败: {close_err}")
    
    def process_single_item(self, emby_item_id: str, force_reprocess_this_item: bool = False) -> bool:
        """
        处理单个 Emby 媒体项目。
        如果项目是剧集 (Series)，则会递归处理其下的所有剧集 (Episodes)。
        """
        if self.is_stop_requested():
            logger.info(f"任务已请求停止，跳过处理 Item ID: {emby_item_id}")
            return False

        # --- 步骤 0: 获取项目详情并判断类型 ---
        try:
            item_details = emby_handler.get_emby_item_details(emby_item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
            if not item_details:
                logger.error(f"无法获取Emby项目 {emby_item_id} 的详情，处理中止。")
                self.save_to_failed_log(emby_item_id, f"未知项目(ID:{emby_item_id})", "无法获取Emby项目详情")
                return False
        except Exception as e:
            logger.error(f"获取Emby项目 {emby_item_id} 详情时发生异常: {e}", exc_info=True)
            self.save_to_failed_log(emby_item_id, f"未知项目(ID:{emby_item_id})", f"获取Emby详情异常: {e}")
            return False

        item_type = item_details.get("Type")
        item_name_for_log = item_details.get("Name", f"ID:{emby_item_id}")

        # --- 步骤 1: 根据类型决定处理方式 ---
        if item_type == "Series":
            logger.info(f"检测到项目 '{item_name_for_log}' (ID: {emby_item_id}) 是一个剧集，将开始递归处理其所有剧集...")
            
            # 首先，处理剧集本身的演员表（主演）
            logger.info(f"--- 正在处理剧集 '{item_name_for_log}' 本身的演员信息 ---")
            self._process_item_core_logic(item_details, force_reprocess_this_item)
            
            # 然后，获取并处理所有剧集
            episodes = emby_handler.get_episodes_for_series(emby_item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
            if episodes is None:
                logger.error(f"获取剧集 '{item_name_for_log}' 的剧集列表失败。")
                return False # 或者可以只标记剧集本身处理失败

            total_episodes = len(episodes)
            logger.info(f"共找到 {total_episodes} 集，开始逐一处理...")
            for i, episode in enumerate(episodes):
                if self.is_stop_requested():
                    logger.info("剧集递归处理被中断。")
                    return False
                
                episode_id = episode.get("Id")
                episode_name = episode.get("Name", f"ID:{episode_id}")
                logger.info(f"--- 正在处理第 {i+1}/{total_episodes} 集: '{episode_name}' (ID: {episode_id}) ---")
                self._process_item_core_logic(episode, force_reprocess_this_item)
                
                # 项目间延迟
                delay = float(self.config.get("delay_between_items_sec", 0.5))
                if delay > 0 and i < total_episodes - 1:
                    time.sleep(delay)
            
            logger.info(f"剧集 '{item_name_for_log}' 的所有剧集处理完毕。")
            return True

        elif item_type == "Movie":
            logger.info(f"检测到项目 '{item_name_for_log}' (ID: {emby_item_id}) 是一部电影，将进行标准处理...")
            return self._process_item_core_logic(item_details, force_reprocess_this_item)
            
        else:
            logger.warning(f"项目 '{item_name_for_log}' (ID: {emby_item_id}) 的类型为 '{item_type}'，当前逻辑只处理 Movie 和 Series，已跳过。")
            return True # 视为处理成功，因为它不需要处理

    def _process_item_core_logic(self, item_details: Dict[str, Any], force_reprocess_this_item: bool = False) -> bool:
        """
        处理单个媒体项（电影或单集）的核心逻辑。
        这是从 process_single_item 中剥离出来的可复用部分。
        """
        item_id = item_details.get("Id")
        if not item_id:
            logger.error("核心处理逻辑失败：传入的 item_details 缺少 ID。")
            return False

        if not force_reprocess_this_item and item_id in self.processed_items_cache:
            logger.info(f"Item ID '{item_id}' ('{item_details.get('Name')}') 已处理过且未强制重处理，跳过。")
            return True

        # --- 后续的逻辑就是你原来 process_single_item 的核心部分 ---
        item_name_for_log = item_details.get("Name", f"未知项目(ID:{item_id})")
        item_type_for_log = item_details.get("Type", "未知类型")
        current_emby_cast_raw = item_details.get("People", [])
        original_emby_cast_count = len(current_emby_cast_raw)
        logger.info(f"媒体 '{item_name_for_log}' 原始Emby People数量: {original_emby_cast_count}")

        # ... (调用 _process_cast_list, _evaluate_cast_processing_quality, update_emby_item_cast, save_to_log 等所有逻辑) ...
        # ... (这部分代码完全复用你已有的，从 "final_cast_for_item = self._process_cast_list(...)" 开始) ...
        
        final_cast_for_item = self._process_cast_list(current_emby_cast_raw, item_details)
        
        logger.info("开始前置步骤：检查并更新被翻译的演员名字...")
        original_names_map = {p.get("Id"): p.get("Name") for p in current_emby_cast_raw if p.get("Id")}
        for actor in final_cast_for_item:
            if self.is_stop_requested(): break
            actor_id = actor.get("EmbyPersonId")
            new_name = actor.get("Name")
            original_name = original_names_map.get(actor_id)
            if actor_id and new_name and original_name and new_name != original_name:
                emby_handler.update_person_details(
                    person_id=actor_id,
                    new_data={"Name": new_name},
                    emby_server_url=self.emby_url,
                    emby_api_key=self.emby_api_key,
                    user_id=self.emby_user_id
                )
        logger.info("演员名字前置更新检查完成。")

        processing_score = self._evaluate_cast_processing_quality(final_cast_for_item, original_emby_cast_count)
        
        update_success = False
        if not final_cast_for_item and original_emby_cast_count > 0:
            logger.warning(f"媒体 '{item_name_for_log}' 处理后演员列表为空，将不执行Emby更新。")
        else:
            cast_for_emby_handler = [
                {"name": actor.get("Name"), "character": actor.get("Role"), "emby_person_id": actor.get("EmbyPersonId"), "provider_ids": actor.get("ProviderIds")}
                for actor in final_cast_for_item
            ]
            update_success = emby_handler.update_emby_item_cast(
                item_id=item_id,
                new_cast_list_for_handler=cast_for_emby_handler,
                emby_server_url=self.emby_url,
                emby_api_key=self.emby_api_key,
                user_id=self.emby_user_id
            )

        if update_success:
            MIN_SCORE_FOR_REVIEW = float(self.config.get("min_score_for_review", 6.0))
            if processing_score < MIN_SCORE_FOR_REVIEW:
                self.save_to_failed_log(item_id, item_name_for_log, f"处理评分过低({processing_score:.1f})", item_type_for_log, score=processing_score)
            else:
                self._remove_from_failed_log_if_exists(item_id)
            self.save_to_processed_log(item_id, item_name_for_log, score=processing_score)
            return True
        else:
            self.save_to_failed_log(item_id, item_name_for_log, "更新Emby演员信息失败", item_type_for_log, score=processing_score)
            return False
        
    def process_item_with_manual_cast(self, item_id: str, manual_cast_list: List[Dict[str, Any]]) -> bool:
        """
        使用手动编辑的演员列表来处理单个媒体项目。
        这个函数是为手动编辑 API 设计的，它会执行完整的“两步更新+锁定刷新”流程。
        """
        logger.info(f"开始使用手动编辑的演员列表处理 Item ID: {item_id}")
        
        # 1. 获取原始 Emby 详情，用于对比名字变更
        try:
            item_details = emby_handler.get_emby_item_details(item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
            if not item_details:
                logger.error(f"手动处理失败：无法获取项目 {item_id} 的详情。")
                return False
            current_emby_cast_raw = item_details.get("People", [])
            item_name_for_log = item_details.get("Name", f"ID:{item_id}")
            item_type_for_log = item_details.get("Type", "Unknown")
        except Exception as e:
            logger.error(f"手动处理失败：获取项目 {item_id} 详情时异常: {e}")
            return False

        # 2. 前置更新：更新被手动修改了名字的 Person 条目
        logger.info("手动处理：开始前置更新演员名字...")
        original_names_map = {p.get("Id"): p.get("Name") for p in current_emby_cast_raw if p.get("Id")}
        for actor in manual_cast_list:
            actor_id = actor.get("emby_person_id")
            new_name = actor.get("name")
            original_name = original_names_map.get(actor_id)
            if actor_id and new_name and original_name and new_name != original_name:
                logger.info(f"手动处理：检测到名字变更 '{original_name}' -> '{new_name}' (ID: {actor_id})")
                emby_handler.update_person_details(
                    person_id=actor_id,
                    new_data={"Name": new_name},
                    emby_server_url=self.emby_url,
                    emby_api_key=self.emby_api_key,
                    user_id=self.emby_user_id
                )
        logger.info("手动处理：演员名字前置更新完成。")

        # 3. 最终更新：将手动编辑的列表更新到媒体
        logger.info(f"手动处理：准备将 {len(manual_cast_list)} 位演员更新到 Emby...")
        update_success = emby_handler.update_emby_item_cast(
            item_id=item_id,
            new_cast_list_for_handler=manual_cast_list,
            emby_server_url=self.emby_url,
            emby_api_key=self.emby_api_key,
            user_id=self.emby_user_id
        )

        if not update_success:
            logger.error(f"手动处理失败：更新 Emby 项目 {item_id} 演员信息时失败。")
            return False

        # # 4. 收尾工作：刷新EMBY
        # logger.info(f"手动处理：演员更新成功，准备锁定并刷新...")
        # if self.config.get("lock_cast_before_refresh", True):
        #     emby_handler.lock_emby_item_field(item_id, "Cast", self.emby_url, self.emby_api_key, self.emby_user_id)
        #     emby_handler.refresh_emby_item_metadata(item_id, self.emby_url, self.emby_api_key, recursive=(item_type_for_log == "Series"))
        
        logger.info(f"手动处理 Item ID: {item_id} ('{item_name_for_log}') 完成。")
        return True
        
    def _remove_from_failed_log_if_exists(self, item_id: str):
        """如果 item_id 存在于 failed_log 中，则删除它。"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM failed_log WHERE item_id = ?", (item_id,))
            if cursor.rowcount > 0:
                logger.info(f"Item ID '{item_id}' 已从 failed_log 中移除 (因本次处理评分达标)。")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"从 failed_log 删除 Item ID '{item_id}' 时失败: {e}", exc_info=True)

    def _format_tmdb_person_for_emby_handler(self, tmdb_person_data: Dict[str, Any],
                                             role_from_source: Optional[str] = None
                                             ) -> Dict[str, Any]:
        """
        将从 TMDb API 获取的演员数据格式化。
        主要名字直接使用 TMDb 返回的 name。
        """
        tmdb_id_str = str(tmdb_person_data.get("id")) if tmdb_person_data.get("id") else None
        final_role = utils.clean_character_name_static(role_from_source) if role_from_source and role_from_source.strip() else "演员"

        name_from_tmdb = tmdb_person_data.get("name") # 直接使用 TMDb 的名字
        # original_name 可以是 TMDb 的 name (如果它是外文)，或者也用 name_from_tmdb
        # 如果 tmdb_person_data 中有 'original_name' 字段且不同于 'name'，也可以考虑使用
        original_name_to_use = tmdb_person_data.get("original_name", name_from_tmdb)


        actor_entry = {
            "Name": name_from_tmdb, # <--- 使用 TMDb 的名字
            "OriginalName": original_name_to_use,
            "Role": final_role,
            "Type": "Actor", # Emby 需要这个
            "ProviderIds": {},
            "EmbyPersonId": None, # 新增演员，初始没有 Emby Person ID
            "TmdbPersonId": tmdb_id_str, # 存储 TMDb Person ID
            "DoubanCelebrityId": None, # 初始设为 None，如果源数据有，后面会填充
            "ProfileImagePathTMDb": tmdb_person_data.get("profile_path"),
            "_source": "tmdb_added_or_enhanced" # 标记来源
        }
        if tmdb_id_str:
            actor_entry["ProviderIds"]["Tmdb"] = tmdb_id_str

        external_ids = tmdb_person_data.get("external_ids", {}) # 来自 get_person_details_tmdb
        tmdb_imdb_id = external_ids.get("imdb_id")
        if tmdb_imdb_id:
            actor_entry["ProviderIds"]["Imdb"] = tmdb_imdb_id
        
        # 如果 tmdb_person_data 中有 translations，可以考虑提取中文名作为 Name (如果 Name 当前是外文)
        # 例如:
        # translations = tmdb_person_data.get("translations", {}).get("translations", [])
        # for trans in translations:
        #     if trans.get("iso_639_1") == "zh" and trans.get("data", {}).get("name"):
        #         actor_entry["Name"] = trans["data"]["name"]
        #         logger.debug(f"  使用TMDb的中文翻译 '{actor_entry['Name']}' 替换/作为演员名。")
        #         break # 通常取第一个中文翻译即可

        return actor_entry

    def process_full_library(self, update_status_callback: Optional[callable] = None, force_reprocess_all: bool = False):
        self.clear_stop_signal() # 清除任何可能残留的停止信号
        logger.debug(f"process_full_library: 方法开始执行。")
        logger.debug(f"  Initial self.libraries_to_process (来自实例属性): {self.libraries_to_process}")
        logger.debug(f"  force_reprocess_all: {force_reprocess_all}")

        if force_reprocess_all:
            logger.info("用户请求强制重处理所有媒体项，将清除数据库中的已处理记录。")
            self.clear_processed_log() # 这个方法应该清除数据库和内存缓存

        if not all([self.emby_url, self.emby_api_key, self.emby_user_id]):
            logger.error("Emby配置不完整，无法处理整个媒体库。")
            if update_status_callback:
                update_status_callback(-1, "Emby配置不完整")
            return

        # --- 关键检查点：从实例属性获取要处理的库列表 ---
        current_libs_to_process = self.libraries_to_process
        logger.info(f"process_full_library: 即将用于获取项目的媒体库ID列表为: {current_libs_to_process}")

        if not current_libs_to_process: # 检查列表是否为空
            logger.warning("process_full_library: 配置中要处理的媒体库ID列表 (current_libs_to_process) 为空。将不会从Emby获取任何项目。")
            if update_status_callback:
                update_status_callback(100, "未在配置中指定要处理的媒体库，或列表为空。")
            # 注意：emby_handler.get_emby_library_items 如果接收到空的 library_ids 列表，它自己也会返回空列表。
            # 所以这里的行为是正确的，后续 movies 和 series_list 会是空。
            # 如果你希望在这种情况下完全不执行后续步骤，可以在这里直接 return。
            # return
        # --- 关键检查点结束 ---

        logger.info(f"开始全量处理选定的Emby媒体库 (ID(s): {current_libs_to_process if current_libs_to_process else '无特定库'})...")
        if update_status_callback:
            update_status_callback(0, "正在获取电影列表...")

        movies: Optional[List[Dict[str, Any]]] = None
        series_list: Optional[List[Dict[str, Any]]] = None

        try:
            movies = emby_handler.get_emby_library_items(
                self.emby_url, self.emby_api_key, "Movie", self.emby_user_id,
                library_ids=current_libs_to_process # 使用局部变量传递
            )
        except Exception as e_movie_get:
            logger.error(f"获取电影列表时发生严重错误: {e_movie_get}", exc_info=True)
            movies = [] # 出错则视为空列表

        if self.is_stop_requested():
            logger.info("获取电影列表后检测到停止信号，处理中止。")
            if update_status_callback: update_status_callback(background_task_status.get("progress", 5), "任务已中断") # type: ignore
            return

        if update_status_callback:
            update_status_callback(5, "正在获取剧集列表...") # 假设电影占5%进度

        try:
            series_list = emby_handler.get_emby_library_items(
                self.emby_url, self.emby_api_key, "Series", self.emby_user_id,
                library_ids=current_libs_to_process # 使用局部变量传递
            )
        except Exception as e_series_get:
            logger.error(f"获取剧集列表时发生严重错误: {e_series_get}", exc_info=True)
            series_list = [] # 出错则视为空列表


        if self.is_stop_requested():
            logger.info("获取剧集列表后检测到停止信号，处理中止。")
            if update_status_callback: update_status_callback(background_task_status.get("progress", 10), "任务已中断") # type: ignore
            return

        # 合并电影和剧集列表，并确保它们是列表类型
        all_items = (movies if isinstance(movies, list) else []) + \
                    (series_list if isinstance(series_list, list) else [])
        total_items = len(all_items)

        if total_items == 0:
            logger.info("从选定的媒体库中未获取到任何电影或剧集项目，或者获取失败，无需进一步处理。")
            if update_status_callback:
                update_status_callback(100, "未在选定库中找到项目或获取失败。")
            return

        logger.info(f"总共从选定的库中获取到 {len(movies) if movies else 0} 部电影和 {len(series_list) if series_list else 0} 部剧集，共 {total_items} 个项目进行处理。")

        for i, item in enumerate(all_items):
            if self.is_stop_requested():
                logger.info("全量媒体库处理在项目迭代中被用户中断。")
                if update_status_callback:
                    current_progress_on_stop = int(((i) / total_items) * 100) if total_items > 0 else 0
                    update_status_callback(current_progress_on_stop, "任务已中断")
                break # 跳出循环

            item_id = item.get('Id')
            item_name = item.get('Name', f"未知项目(ID:{item_id})")
            item_type_str = "电影" if item.get("Type") == "Movie" else ("剧集" if item.get("Type") == "Series" else "未知类型")

            # 更新进度 (0-99% 用于处理过程，100% 用于完成)
            # 这里的进度是基于总项目数的，而不是获取列表的进度
            progress_percent = int(((i + 1) / total_items) * 90) + 10 # 假设获取列表占了前10%
            if progress_percent > 99: progress_percent = 99


            message = f"正在处理 {item_type_str} ({i+1}/{total_items}): {item_name}"
            logger.info(message)
            if update_status_callback:
                update_status_callback(progress_percent, message)

            if not item_id:
                logger.warning(f"条目缺少ID，跳过: {item_name}")
                continue

            # 调用 process_single_item 处理单个项目
            # force_reprocess_this_item 参数现在由 force_reprocess_all 控制
            process_success = self.process_single_item(item_id, force_reprocess_this_item=force_reprocess_all)

            if not process_success and self.is_stop_requested():
                # 如果处理单个项目失败是因为收到了停止信号
                logger.info(f"处理 Item ID {item_id} ('{item_name}') 时被中断，停止全量扫描。")
                if update_status_callback:
                    update_status_callback(progress_percent, f"处理 '{item_name}' 时中断")
                break # 跳出循环

            # 项目间延迟
            delay = float(self.config.get("delay_between_items_sec", 0.5))
            if delay > 0 and i < total_items - 1: # 最后一个项目之后不延迟
                if self.is_stop_requested():
                    logger.info("项目间延迟等待前检测到停止信号，中断全量扫描。")
                    if update_status_callback: update_status_callback(progress_percent, "任务已中断")
                    break
                time.sleep(delay)

        # 循环结束后的最终状态报告
        if self.is_stop_requested():
            logger.info("全量处理任务已结束（因用户请求停止）。")
            # 状态已在循环内或 _execute_task_with_lock 的 finally 中更新
        else:
            logger.info("全量处理Emby媒体库结束。")
            if update_status_callback:
                update_status_callback(100, "全量处理完成。")


    def close(self):
        """关闭 MediaProcessor 实例，例如关闭数据库连接池或释放其他资源。"""
        if self.douban_api and hasattr(self.douban_api, 'close'):
            logger.info("正在关闭 MediaProcessor 中的 DoubanApi session...")
            self.douban_api.close()
        # 如果有其他需要关闭的资源，例如数据库连接池（如果使用的话），在这里关闭
        logger.info("MediaProcessor close 方法执行完毕。")

    def enrich_cast_list(self, current_cast: List[Dict[str, Any]], new_cast_from_web: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        使用从网页提取的新列表来丰富（Enrich）当前演员列表。
        采用反向翻译匹配：将当前演员的中文名翻译成英文，再去匹配网页提取的英文列表。
        """
        logger.info(f"开始“仅丰富”模式（反向翻译匹配）：使用 {len(new_cast_from_web)} 位新演员信息来更新 {len(current_cast)} 位当前演员。")
        
        enriched_cast = [dict(actor) for actor in current_cast]
        used_new_actor_indices = set()
        
        # 我们需要一个数据库连接来调用翻译缓存和在线翻译
        conn = self._get_db_connection()
        cursor = conn.cursor()

        try:
            for i, current_actor in enumerate(enriched_cast):
                current_name_zh = current_actor.get('name', '').strip()
                if not current_name_zh or not utils.contains_chinese(current_name_zh):
                    # 如果当前演员名不是中文，就直接进行常规匹配
                    logger.debug(f"当前演员 '{current_name_zh}' 非中文，使用直接匹配。")
                    # (这里可以保留之前的直接匹配逻辑，为简化，我们先专注解决中文名问题)
                    pass
                
                # --- 核心：反向翻译匹配 ---
                # 将当前演员的中文名翻译成英文
                # 注意：_translate_actor_field 内部会自动处理缓存
                translated_name_en = self._translate_actor_field(
                    text=current_name_zh,
                    field_name="演员名(用于匹配)",
                    actor_name_for_log=current_name_zh,
                    db_cursor_for_cache=cursor
                )
                
                # 如果翻译结果和原文一样（说明翻译失败或已经是英文），则跳过这个演员
                if translated_name_en == current_name_zh:
                    logger.debug(f"演员 '{current_name_zh}' 翻译失败或无需翻译，跳过反向匹配。")
                    continue

                logger.info(f"尝试为 '{current_name_zh}' (翻译为: '{translated_name_en}') 寻找匹配...")

                # 在新列表中寻找能与翻译后的英文名匹配的项
                for j, new_actor in enumerate(new_cast_from_web):
                    if j in used_new_actor_indices:
                        continue

                    new_name_en = new_actor.get('name', '').strip()
                    if not new_name_en:
                        continue

                    # 进行不区分大小写的匹配
                    if translated_name_en.lower() == new_name_en.lower():
                        logger.info(f"匹配成功: '{current_name_zh}' <=> '{new_name_en}' (通过翻译)")
                        
                        new_role = new_actor.get('role')
                        if new_role:
                            logger.info(f"  -> 角色名更新: '{current_actor.get('role')}' -> '{new_role}'")
                            enriched_cast[i]['role'] = new_role
                        
                        enriched_cast[i]['matchStatus'] = '已更新(翻译匹配)'
                        used_new_actor_indices.add(j)
                        break # 找到匹配，处理下一个当前演员

            # 提交可能在翻译过程中产生的数据库缓存更新
            conn.commit()

        except Exception as e:
            logger.error(f"在 enrich_cast_list 中发生错误: {e}", exc_info=True)
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

        unmatched_count = len(new_cast_from_web) - len(used_new_actor_indices)
        if unmatched_count > 0:
            logger.info(f"{unmatched_count} 位从网页提取的演员未能匹配任何现有演员，已被丢弃。")

        logger.info(f"“仅丰富”模式完成，返回 {len(enriched_cast)} 位演员。")
        return enriched_cast
class SyncHandler:
    def __init__(self, db_path: str, emby_url: str, emby_api_key: str, emby_user_id: Optional[str]):
        self.db_path = db_path
        self.emby_url = emby_url
        self.emby_api_key = emby_api_key
        self.emby_user_id = emby_user_id
        logger.info(f"SyncHandler initialized. DB: {db_path}, Emby: {emby_url}")

    def _get_db_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def sync_emby_person_map_to_db(self, update_status_callback: Optional[callable] = None):
        logger.info("开始同步 Emby Person 映射表到本地数据库...")
        if update_status_callback: update_status_callback(0, "正在从Emby获取所有人物信息...")

        persons_from_emby = emby_handler.get_all_persons_from_emby(
            self.emby_url, self.emby_api_key, self.emby_user_id
        )
        
        if persons_from_emby is None:
            logger.error("同步映射表失败：无法从 Emby 获取 Person 列表。")
            if update_status_callback: update_status_callback(-1, "从Emby获取人物信息失败")
            return
        if not persons_from_emby:
            logger.info("Emby 中没有找到任何 Person 条目。")
            if update_status_callback: update_status_callback(100, "Emby中无人物信息")
            return

        conn = self._get_db_conn()
        
        stats = {
            "total_from_emby_api": len(persons_from_emby),
            "processed": 0,
            "inserted": 0,
            "updated": 0,
            "skipped_no_emby_id": 0,
            "db_errors": 0
        }

        logger.info(f"从Emby API获取到 {stats['total_from_emby_api']} 个 Person 条目，开始处理并同步...")

        try:
            cursor = conn.cursor()

            for idx, person_emby in enumerate(persons_from_emby):
                stats["processed"] += 1
                if update_status_callback and idx > 0 and idx % 100 == 0:
                    progress = int(((idx + 1) / stats['total_from_emby_api']) * 100)
                    update_status_callback(progress, f"正在处理第 {idx+1}/{stats['total_from_emby_api']} 个人物...")

                emby_pid = str(person_emby.get("Id", "")).strip()
                emby_name = str(person_emby.get("Name", "")).strip()
                
                if not emby_pid: 
                    logger.debug(f"跳过Emby Person (Name: '{emby_name}')，缺少 Emby Person ID。")
                    stats["skipped_no_emby_id"] += 1
                    continue
                
                provider_ids = person_emby.get("ProviderIds", {})
                tmdb_pid = str(provider_ids.get("Tmdb", "")).strip() or None
                douban_pid = str(provider_ids.get("Douban", "")).strip() or None
                imdb_pid = str(provider_ids.get("Imdb", "")).strip() or None
                
                # 为 tmdb_name 和 douban_name 准备值 (保持你原来的逻辑)
                current_tmdb_name = emby_name if tmdb_pid else None
                current_douban_name = emby_name if douban_pid else None

                logger.debug(f"处理 Emby Person: EmbyPID='{emby_pid}', Name='{emby_name}', TMDb='{tmdb_pid}', IMDb='{imdb_pid}', Douban='{douban_pid}'")
                
                # --- 查询现有记录的逻辑保持不变 ---
                existing_map_id = None
                sql_select_parts = []
                select_params = []
                if emby_pid:
                    sql_select_parts.append("emby_person_id = ?")
                    select_params.append(emby_pid)
                # ... (其他 ID 的 select 条件不变) ...
                if imdb_pid:
                    sql_select_parts.append("imdb_id = ?")
                    select_params.append(imdb_pid)
                if tmdb_pid:
                    sql_select_parts.append("tmdb_person_id = ?")
                    select_params.append(tmdb_pid)
                if douban_pid:
                    sql_select_parts.append("douban_celebrity_id = ?")
                    select_params.append(douban_pid)

                found_entry_for_update = None
                if sql_select_parts:
                    query_condition = " OR ".join(sql_select_parts)
                    # 从数据库中多获取一些字段，用于更新时的比较或保留旧值
                    cursor.execute(f"SELECT map_id, emby_person_id, emby_person_name, imdb_id, tmdb_person_id, tmdb_name, douban_celebrity_id, douban_name FROM person_identity_map WHERE {query_condition}", tuple(select_params))
                    found_entry_for_update = cursor.fetchone() 
                
                # --- 数据库操作开始 ---
                try: # 包裹单条记录的数据库操作
                    if found_entry_for_update:
                        existing_map_id = found_entry_for_update["map_id"]
                        logger.debug(f"  找到映射表记录 (map_id: {existing_map_id}) for EmbyPID '{emby_pid}' (或其关联ID)。准备更新。")
                        
                        # 构建更新的字段和值 (确保顺序一致)
                        update_data_map = {} # 使用字典来确保键的唯一性，并稍后按固定顺序提取

                        # 总是更新这些
                        update_data_map["emby_person_id"] = emby_pid
                        update_data_map["emby_person_name"] = emby_name
                        
                        # 条件性更新其他ID，如果新ID存在，则使用新ID；否则，保留旧ID
                        update_data_map["imdb_id"] = imdb_pid if imdb_pid is not None else found_entry_for_update.get("imdb_id")
                        update_data_map["tmdb_person_id"] = tmdb_pid if tmdb_pid is not None else found_entry_for_update.get("tmdb_person_id")
                        update_data_map["douban_celebrity_id"] = douban_pid if douban_pid is not None else found_entry_for_update.get("douban_celebrity_id")
                        
                        # 更新对应的名字 (如果对应的ID存在，则用emby_name，否则保留旧名字)
                        update_data_map["tmdb_name"] = current_tmdb_name if tmdb_pid is not None else found_entry_for_update.get("tmdb_name")
                        update_data_map["douban_name"] = current_douban_name if douban_pid is not None else found_entry_for_update.get("douban_name")

                        # 定义更新列的固定顺序 (不包括时间戳，它们由SQL处理)
                        update_columns_ordered = [
                            "emby_person_id", "emby_person_name", 
                            "imdb_id", "tmdb_person_id", "douban_celebrity_id",
                            "tmdb_name", "douban_name"
                        ]
                        
                        set_clauses = [f"{col} = ?" for col in update_columns_ordered]
                        update_values = [update_data_map.get(col) for col in update_columns_ordered] # 按固定顺序提取值

                        # 添加时间戳更新
                        set_clauses.extend(["last_synced_at = CURRENT_TIMESTAMP", "last_updated_at = CURRENT_TIMESTAMP"])
                        
                        sql_update = f"UPDATE person_identity_map SET {', '.join(set_clauses)} WHERE map_id = ?"
                        update_values.append(existing_map_id) # map_id 作为最后一个参数
                        
                        logger.debug(f"    Executing UPDATE for map_id: {existing_map_id} with SQL: {sql_update} and PARAMS: {tuple(update_values)}")
                        cursor.execute(sql_update, tuple(update_values))
                        if cursor.rowcount > 0: stats["updated"] += 1
                        logger.debug(f"    映射表记录 (map_id: {existing_map_id}) 已更新。Rowcount: {cursor.rowcount}")

                    else: # INSERT
                        logger.debug(f"  未找到EmbyPID '{emby_pid}' (或其关联ID) 的映射表记录，准备插入。")
                        
                        # 定义插入列的固定顺序和对应的值
                        insert_data_map = {
                            "emby_person_id": emby_pid,
                            "emby_person_name": emby_name,
                            "imdb_id": imdb_pid,
                            "tmdb_person_id": tmdb_pid,
                            "douban_celebrity_id": douban_pid,
                            "tmdb_name": current_tmdb_name,
                            "douban_name": current_douban_name
                            # 时间戳由SQL处理
                        }
                        
                        # 固定的列顺序 (不包括时间戳)
                        insert_columns_ordered = [
                            "emby_person_id", "emby_person_name",
                            "imdb_id", "tmdb_person_id", "douban_celebrity_id",
                            "tmdb_name", "douban_name"
                        ]
                        
                        final_insert_cols = list(insert_columns_ordered) # 复制一份用于添加时间戳列名
                        final_insert_vals = [insert_data_map.get(col) for col in insert_columns_ordered] # 按固定顺序提取值
                        
                        final_insert_cols.extend(["last_synced_at", "last_updated_at"])
                        placeholders = ["?" for _ in final_insert_vals] + ["CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP"]
                        
                        sql_insert = f"INSERT INTO person_identity_map ({', '.join(final_insert_cols)}) VALUES ({', '.join(placeholders)})"
                        
                        logger.debug(f"    Executing INSERT for EmbyPID: {emby_pid} with SQL: {sql_insert} and PARAMS: {tuple(final_insert_vals)}")
                        cursor.execute(sql_insert, tuple(final_insert_vals))
                        if cursor.rowcount > 0: stats["inserted"] += 1
                        logger.info(f"    新增映射到表: EmbyPID='{emby_pid}', Name='{emby_name}', TMDb='{tmdb_pid}', IMDb='{imdb_pid}', Douban='{douban_pid}'. Rowcount: {cursor.rowcount}")

                except sqlite3.IntegrityError as e_int:
                    logger.warning(f"    处理EmbyPID '{emby_pid}' 时发生完整性冲突: {e_int}。可能是emby_person_id已存在但查询逻辑未完全匹配。")
                    stats["db_errors"] += 1
                    # 不在这里 rollback，让事务继续，错误已被记录
                except sqlite3.Error as e_db_op:
                    logger.error(f"    处理EmbyPID '{emby_pid}' 时数据库操作失败: {e_db_op}", exc_info=True)
                    stats["db_errors"] += 1
                    # 不在这里 rollback
                # --- 数据库操作结束 ---
                
                if idx > 0 and idx % 500 == 0: # 每500条Emby Person记录处理后提交一次
                    logger.info(f"已处理 {idx+1} 条Emby Person记录，准备提交数据库更改...")
                    conn.commit()
                    logger.info(f"数据库已提交。")

            # 循环结束后，提交所有剩余的更改
            logger.info("所有Emby Person记录处理完毕，准备进行最终数据库提交...")
            conn.commit() 
            logger.info("最终数据库提交完成。")

        except Exception as e_outer:
            logger.error(f"同步映射表主循环发生错误: {e_outer}", exc_info=True)
            if conn:
                try:
                    logger.warning("主循环错误，尝试回滚当前未提交的事务...")
                    conn.rollback()
                    logger.info("当前未提交的事务已回滚。")
                except Exception as rb_err:
                    logger.error(f"主循环错误后回滚失败: {rb_err}")
            stats["db_errors"] +=1 # 确保错误计数，或根据情况调整
        finally:
            if conn:
                try: conn.close()
                except Exception as close_e: logger.error(f"SyncHandler: 关闭数据库连接失败: {close_e}")
        
        # ... (后续的统计日志和 update_status_callback 不变) ...
        logger.info("--- Emby Person 映射表同步统计 ---")
        logger.info(f"从 Emby API 共获取 Person 条目数: {stats['total_from_emby_api']}")
        logger.info(f"实际处理的 Emby Person 条目数: {stats['processed']}")
        logger.info(f"因缺少 Emby Person ID 而跳过: {stats['skipped_no_emby_id']}")
        logger.info(f"本次同步新增到映射表的条目数: {stats['inserted']}")
        logger.info(f"本次同步更新映射表中已有条目的数量: {stats['updated']}")
        logger.info(f"数据库操作错误数: {stats['db_errors']}")
        logger.info("------------------------------------")

        if update_status_callback:
            if stats["db_errors"] > 0:
                update_status_callback(-1, f"映射表同步部分完成但有{stats['db_errors']}个错误。新增{stats['inserted']}, 更新{stats['updated']}。")
            else:
                update_status_callback(100, f"映射表同步完成。新增{stats['inserted']}, 更新{stats['updated']}。")
# core_processor_api.py
import time
import re
import os
import json
import sqlite3 # 用于数据库操作
from typing import Dict, List, Optional, Any, Tuple
import threading
import tmdb_handler
from douban import DoubanApi, clean_character_name_static
# 假设 emby_handler.py, utils.py, logger_setup.py, constants.py 都在同一级别或Python路径中
import emby_handler
import utils # 导入我们上面修改的 utils.py
import constants
import logging
from ai_translator import AITranslator # ✨✨✨ 导入新的AI翻译器 ✨✨✨
from actor_utils import ActorDBManager
# DoubanApi 的导入和可用性检查
logger = logging.getLogger(__name__)
try:
    from douban import DoubanApi # douban.py 现在也使用数据库
    DOUBAN_API_AVAILABLE = True
    logger.debug("DoubanApi 模块已成功导入到 core_processor。")
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

 
class MediaProcessorAPI:
    # ✨✨✨初始化✨✨✨
    def __init__(self, config: Dict[str, Any]):
        # config 这个参数，是已经从 load_config() 加载好的、一个标准的 Python 字典
        self.config = config
        self.db_path = config.get('db_path')
        if not self.db_path:
            logger.error("MediaProcessorAPI 初始化失败：未在配置中找到 'db_path'。")
            raise ValueError("数据库路径 (db_path) 未在配置中提供给 MediaProcessorAPI。")
        # 初始化我们的数据库管理员
        self.actor_db_manager = ActorDBManager(self.db_path)
        logger.debug("ActorDBManager 实例已在 MediaProcessorAPI 中创建。")

        self.douban_api = None
        if getattr(constants, 'DOUBAN_API_AVAILABLE', False):
            try:
                self.douban_api = DoubanApi(db_path=self.db_path)
                logger.debug("DoubanApi 实例已在 MediaProcessorAPI 中创建。")
            except Exception as e:
                logger.error(f"MediaProcessorAPI 初始化 DoubanApi 失败: {e}", exc_info=True)
        else:
            logger.warning("DoubanApi 常量指示不可用，将不使用豆瓣功能。")

        # 从 config 字典中安全地获取所有配置
        self.emby_url = self.config.get("emby_server_url")
        self.emby_api_key = self.config.get("emby_api_key")
        self.emby_user_id = self.config.get("emby_user_id")
        self.tmdb_api_key = self.config.get("tmdb_api_key", "")
        self.translator_engines = self.config.get("translator_engines_order", constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
        # self.data_source_mode = self.config.get("data_source_mode", constants.DEFAULT_DOMESTIC_SOURCE_MODE)
        self.local_data_path = self.config.get("local_data_path", "").strip()
        self.libraries_to_process = self.config.get("libraries_to_process", [])

        self._stop_event = threading.Event()
        self.processed_items_cache = self._load_processed_log_from_db()

        # ✨✨✨ 关键修复：从 config 字典中获取AI配置 ✨✨✨
        self.ai_translator = None
        # .get("ai_translation_enabled", False) 会安全地处理键不存在的情况
        self.ai_translation_enabled = self.config.get("ai_translation_enabled", False) 
        
        if self.ai_translation_enabled:
            try:
                # 把整个 config 字典传给 AITranslator，让它自己去取需要的配置
                self.ai_translator = AITranslator(self.config)
                logger.info("AI翻译器已成功初始化并启用。")
            except Exception as e:
                logger.error(f"AI翻译器初始化失败，将禁用AI翻译功能: {e}")
                self.ai_translation_enabled = False # 初始化失败，自动关闭开关
        else:
            logger.info("AI翻译功能未启用。")

        logger.debug(f"MediaProcessorAPI 初始化完成。Emby URL: {self.emby_url}, UserID: {self.emby_user_id}")
        logger.info(f"  TMDb API Key: {'已配置' if self.tmdb_api_key else '未配置'}")
        logger.debug(f"  本地数据源路径: '{self.local_data_path if self.local_data_path else '未配置'}'")
        logger.debug(f"  将处理的媒体库ID: {self.libraries_to_process if self.libraries_to_process else '未指定特定库'}")
        logger.info(f"  已从数据库加载 {len(self.processed_items_cache)} 个已处理媒体记录到内存缓存。")
        logger.debug(f"  INIT - self.local_data_path: '{self.local_data_path}'")
        logger.debug(f"  INIT - self.tmdb_api_key (len): {len(self.tmdb_api_key) if self.tmdb_api_key else 0}")
        logger.debug(f"  INIT - DOUBAN_API_AVAILABLE (from top level): {DOUBAN_API_AVAILABLE}") # 打印顶层导入状态
        logger.debug(f"  INIT - self.douban_api is None: {self.douban_api is None}")
        if self.douban_api:
            logger.debug(f"  INIT - self.douban_api type: {type(self.douban_api)}")
    # ✨✨✨占位符✨✨✨
    def check_and_add_to_watchlist(self, item_details: Dict[str, Any]):
        """
        普通模式下，此功能被禁用。定义一个空方法以保证接口统一，避免报错。
        """
        logger.debug("【普通模式】跳过追剧判断（功能禁用）。")
        pass # 什么也不做，直接返回
    # ✨✨✨新处理单个媒体项（电影、剧集或单集）的核心业务逻辑✨✨✨
    def _process_item_api_mode(self, item_details: Dict[str, Any], process_episodes: bool):
        item_id = item_details.get("Id")
        item_name_for_log = item_details.get("Name")
        item_type = item_details.get("Type")

        logger.debug(f"开始处理: '{item_name_for_log}' (类型: {item_type}) ---")
        
        # a. 获取并处理当前项目的演员表
        current_emby_cast_raw = item_details.get("People", [])
        original_emby_cast_count = len(current_emby_cast_raw)
        
        final_cast_for_item = self._process_cast_list(current_emby_cast_raw, item_details)
        
        # b. 评分
        processing_score = self._evaluate_cast_processing_quality(final_cast_for_item, original_emby_cast_count)

        # c. 持久化当前项目的演员表 (两步更新)
        logger.info("开始前置步骤：检查并更新被翻译的演员名字...")
        original_names_map = {p.get("Id"): p.get("Name") for p in current_emby_cast_raw if p.get("Id")}
        for actor in final_cast_for_item:
            if self.is_stop_requested(): raise InterruptedError("任务中止")
            actor_id = actor.get("EmbyPersonId")
            new_name = actor.get("Name")
            original_name = original_names_map.get(actor_id)
            if actor_id and new_name and original_name and new_name != original_name:
                emby_handler.update_person_details(actor_id, {"Name": new_name}, self.emby_url, self.emby_api_key, self.emby_user_id)
        
        cast_for_handler = [{"name": a.get("Name"), "character": a.get("Role"), "emby_person_id": a.get("EmbyPersonId")} for a in final_cast_for_item]
        update_success = emby_handler.update_emby_item_cast(item_id, cast_for_handler, self.emby_url, self.emby_api_key, self.emby_user_id)
        if not update_success:
            raise RuntimeError(f"普通模式更新项目 '{item_name_for_log}' 演员列表失败")

        # d. 新增逻辑：如果当前是剧集，则将这份演员表注入所有“季”和“分集”
        if item_type == "Series":
            logger.info(f"【普通模式-批量注入】准备将处理好的演员表注入到 '{item_name_for_log}' 的所有子项中...")
            children = emby_handler.get_series_children(item_id, self.emby_url, self.emby_api_key, self.emby_user_id, item_name_for_log)
            
            if children:
                # <<< --- 核心修改：新增季演员表注入逻辑 --- >>>
                seasons = [child for child in children if child.get("Type") == "Season"]
                if seasons:
                    total_seasons = len(seasons)
                    logger.info(f"【普通模式-批量注入】找到 {total_seasons} 个季，将为其注入演员表...")
                    for i, season in enumerate(seasons):
                        if self.is_stop_requested(): raise InterruptedError("任务中止")
                        season_id = season.get("Id")
                        season_name = season.get("Name")
                        logger.debug(f"  ({i+1}/{total_seasons}) 正在为季 '{season_name}' 更新演员表...")
                        
                        emby_handler.update_emby_item_cast(season_id, cast_for_handler, self.emby_url, self.emby_api_key, self.emby_user_id)
                        
                        time.sleep(float(self.config.get("delay_between_items_sec", 0.2)))
                # <<< --- 核心修改结束 --- >>>

                if process_episodes:
                    episodes = [child for child in children if child.get("Type") == "Episode"]
                    if episodes:
                        total_episodes = len(episodes)
                        logger.info(f"【普通模式-批量注入】找到 {total_episodes} 个分集需要更新。")

                        for i, episode in enumerate(episodes):
                            if self.is_stop_requested(): raise InterruptedError("任务中止")
                            episode_id = episode.get("Id")
                            episode_name = episode.get("Name")
                            logger.debug(f"  ({i+1}/{total_episodes}) 正在为分集 '{episode_name}' 更新演员表...")
                            
                            emby_handler.update_emby_item_cast(episode_id, cast_for_handler, self.emby_url, self.emby_api_key, self.emby_user_id)
                            
                            time.sleep(float(self.config.get("delay_between_items_sec", 0.2)))
        
        # ★★★ 在这里，记录主项目日志之前，添加刷新操作 ★★★
        logger.info(f"所有演员信息更新完成，准备为项目 '{item_name_for_log}' 触发元数据刷新...")
        refresh_success = emby_handler.refresh_emby_item_metadata(
            item_emby_id=item_id,
            emby_server_url=self.emby_url,
            emby_api_key=self.emby_api_key,
            replace_all_metadata_param=False, # <-- 普通模式使用“补充缺失”模式
            item_name_for_log=item_name_for_log
        )
        
        if not refresh_success:
            # 即使刷新失败，前面的操作也成功了，所以我们只记录一个警告，不中断流程
            logger.warning(f"【普通模式】为 '{item_name_for_log}' 触发刷新失败，您可能需要稍后在Emby中手动刷新。")

        # e. 记录主项目的日志
        min_score_for_review = float(self.config.get("min_score_for_review", 6.0))
        if item_type in ["Movie", "Series"]:
            if processing_score < min_score_for_review:
                self.save_to_failed_log(item_id, item_name_for_log, f"处理评分过低 ({processing_score:.1f})", item_type, score=processing_score)
            else:
                self.save_to_processed_log(item_id, item_name_for_log, score=processing_score)
                self._remove_from_failed_log_if_exists(item_id)
        else:
            # 对于分集(Episode)和季(Season)，我们不进行评分和独立的完成记录
            # 因为它们的处理是附属于剧集的
            logger.debug(f"项目 '{item_name_for_log}' (类型: {item_type}) 处理完成，不计入独立处理日志。")
    # ✨✨✨获取数据库连接的辅助方法✨✨✨
    def _get_db_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row # 方便按列名访问
        return conn
    # ✨✨✨设置停止信号，用于在多线程环境中优雅地中止长时间运行的任务✨✨✨
    def signal_stop(self):
        logger.info("MediaProcessorAPI 收到停止信号。")
        self._stop_event.set()
    # ✨✨✨清除停止信号，以便开始新的任务✨✨✨
    def clear_stop_signal(self):
        self._stop_event.clear()
        logger.debug("MediaProcessorAPI 停止信号已清除。")
    # ✨✨✨检查是否已请求停止当前任务✨✨✨
    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()
    # ✨✨✨从数据库加载所有已处理过的项目ID到内存缓存中，以提高启动速度和避免重复处理✨✨✨
    def _load_processed_log_from_db(self) -> Dict[str, str]:
        # 这一块的所有内容都需要缩进！
        log_dict = {}
        try:
            # self 前面有4个空格的缩进
            conn = self._get_db_connection()
            cursor = conn.cursor()
            # 同时查询 item_id 和 item_name
            cursor.execute("SELECT item_id, item_name FROM processed_log")
            rows = cursor.fetchall()
            for row in rows:
                # 将 ID 和 名称 存入字典
                if row['item_id'] and row['item_name']:
                    log_dict[row['item_id']] = row['item_name']
            conn.close()
        except Exception as e:
            logger.error(f"从数据库读取已处理记录失败: {e}", exc_info=True)
        
        # return 前面也有4个空格的缩进
        return log_dict
    # ✨✨✨将成功处理的媒体项ID、名称和评分保存到数据库和内存缓存中✨✨✨
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

            # 准备好要存入缓存的名称，避免存入None
            name_for_cache = item_name if item_name else f"未知项目(ID:{item_id})"
            
            # 检查是否是新条目，用于决定日志级别
            is_new_in_cache = item_id not in self.processed_items_cache
            
            # 更新内存缓存（无论新旧，都用最新的名字覆盖）
            self.processed_items_cache[item_id] = name_for_cache

            # 根据是否是新条目打印不同级别的日志
            if is_new_in_cache:
                logger.info(f"Item '{item_name}' (ID: {item_id}) 已添加到已处理记录 (数据库[评分:{score_display}]和内存)。")
            else:
                logger.debug(f"Item '{item_name}' (ID: {item_id}) 已更新/确认在已处理记录 (数据库[评分:{score_display}])。")
        except Exception as e:
            logger.error(f"保存已处理记录到数据库失败 (Item ID: {item_id}): {e}", exc_info=True)
    # ✨✨✨清除数据库中的所有已处理记录以及内存中的缓存✨✨✨
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
    # ✨✨✨将处理失败或评分过低的媒体项信息保存到数据库，以便后续审查或手动处理✨✨✨
    def save_to_failed_log(self, item_id: str, item_name: Optional[str], error_msg: str, item_type: Optional[str] = None, score: Optional[float] = None): 
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
            logger.info(f"('{item_name}') 已作为失败/待手动处理项记录到数据库。原因: {error_msg} {score_info}")
        except Exception as e:
            logger.error(f"保存失败记录到数据库失败 (Item ID: {item_id}): {e}", exc_info=True)
    # ✨✨✨翻译演员的特定字段（如姓名、角色），并智能处理缓存和翻译引擎的选择✨✨✨
    def _translate_actor_field(self, text: Optional[str], field_name: str, 
                               actor_name_for_log: str, 
                               db_cursor_for_cache: sqlite3.Cursor) -> Optional[str]:
        """
        翻译演员的特定字段，智能选择AI或传统翻译引擎。
        """
        if not text or not text.strip():
            return text

        # 检查是否已包含中文，如果是，则不翻译
        if utils.contains_chinese(text):
            return text

        text_stripped = text.strip()
        
        # 跳过单字母或双大写字母
        if len(text_stripped) == 1 and 'A' <= text_stripped.upper() <= 'Z':
            return text
        if len(text_stripped) == 2 and text_stripped.isupper() and text_stripped.isalpha():
            return text

        # 1. 优先从数据库读取缓存
        cached_entry = DoubanApi._get_translation_from_db(text_stripped)
        if cached_entry and cached_entry.get("translated_text"):
            cached_translation = cached_entry.get("translated_text")
            engine_used = cached_entry.get("engine_used")
            logger.info(f"数据库翻译缓存命中 for '{text_stripped}' -> '{cached_translation}' (引擎: {engine_used})")
            return cached_translation

        # 2. 如果缓存未命中，根据配置选择翻译方式
        final_translation = None
        final_engine = "unknown"

        if self.ai_translation_enabled and self.ai_translator:
            # --- 使用AI翻译 ---
            final_translation = self.ai_translator.translate(text_stripped)
            final_engine = self.ai_translator.provider
        else:
            # --- 使用传统翻译引擎 ---
            logger.info(f"AI翻译未启用或初始化失败，回退到传统翻译引擎处理: '{text_stripped}'")
            translation_result = utils.translate_text_with_translators(
                text_stripped,
                engine_order=self.translator_engines
            )
            if translation_result and translation_result.get("text"):
                final_translation = translation_result["text"]
                final_engine = translation_result["engine"]

        # 3. 处理翻译结果
        if final_translation and final_translation.strip() and final_translation.strip().lower() != text_stripped.lower():
            # 翻译成功，存入缓存并返回结果
            DoubanApi._save_translation_to_db(text_stripped, final_translation, final_engine, cursor=db_cursor_for_cache)
            return final_translation
        else:
            # 翻译失败或返回原文，将失败状态存入缓存，并返回原文
            logger.warning(f"所有翻译引擎都未能翻译 '{text_stripped}' 或返回了原文。")
            DoubanApi._save_translation_to_db(text_stripped, None, f"failed_or_same_via_{final_engine}", cursor=db_cursor_for_cache)
            return text
    # ✨✨✨评估处理后的演员列表质量，并返回一个0到10的分数✨✨✨
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
        logger.info(f"  最终评分: {final_score_rounded:.1f} (基于 {total_actors_in_final_list} 位演员的平均分)")
        return final_score_rounded
    # ✨✨✨优先级：有意义的候选角色 > 现有角色 > "演员" > 空✨✨✨    
    def _select_best_role(self, current_role: str, candidate_role: str) -> str:
        """
        根据优先级选择最佳角色名。
        【最终修正版】确保有价值的中文名不会被英文名覆盖。

        优先级顺序:
        1. 有内容的豆瓣中文角色名
        2. 有内容的本地中文角色名  <-- 这是保护您本地数据的关键
        3. 有内容的英文角色名 (候选来源优先)
        4. '演员' (或其他占位符)
        5. 空字符串
        """
        # --- 步骤 1: 清理和规范化输入 ---
        original_current = current_role # 保存原始值用于日志
        original_candidate = candidate_role # 保存原始值用于日志
        
        current_role = str(current_role or '').strip()
        candidate_role = str(candidate_role or '').strip()

        # --- 步骤 2: 准备日志和判断标志 ---
        # 使用 self.logger，如果您的类中是这样命名的
        # 如果不是，请替换为正确的 logger 对象名
        logger.debug(f"--- [角色选择开始] ---")
        logger.debug(f"  输入: current='{original_current}', candidate='{original_candidate}'")
        logger.debug(f"  清理后: current='{current_role}', candidate='{candidate_role}'")

        current_is_chinese = utils.contains_chinese(current_role)
        candidate_is_chinese = utils.contains_chinese(candidate_role)
        
        # 定义一个更广泛的占位符列表
        placeholders = {"actor", "actress", "演员", "配音"}
        current_is_placeholder = current_role.lower() in placeholders
        candidate_is_placeholder = candidate_role.lower() in placeholders

        logger.debug(f"  分析: current_is_chinese={current_is_chinese}, current_is_placeholder={current_is_placeholder}")
        logger.debug(f"  分析: candidate_is_chinese={candidate_is_chinese}, candidate_is_placeholder={candidate_is_placeholder}")

        # --- 步骤 3: 应用优先级规则并记录决策 ---

        # 优先级 1: 候选角色是有效的中文名
        if candidate_is_chinese and not candidate_is_placeholder:
            logger.debug(f"  决策: [优先级1] 候选角色是有效中文名。选择候选角色。")
            logger.debug(f"  选择: '{candidate_role}'")
            logger.debug(f"--- [角色选择结束] ---")
            return candidate_role

        # 优先级 2: 当前角色是有效的中文名，而候选角色不是。必须保留当前角色！
        if current_is_chinese and not current_is_placeholder and not candidate_is_chinese:
            logger.debug(f"  决策: [优先级2] 当前角色是有效中文名，而候选不是。保留当前角色。")
            logger.debug(f"  选择: '{current_role}'")
            logger.debug(f"--- [角色选择结束] ---")
            return current_role

        # 优先级 3: 两者都不是有效的中文名（或都是）。选择一个非占位符的，候选者优先。
        if candidate_role and not candidate_is_placeholder:
            logger.debug(f"  决策: [优先级3a] 候选角色是有效的非中文名/占位符。选择候选角色。")
            logger.debug(f"  选择: '{candidate_role}'")
            logger.debug(f"--- [角色选择结束] ---")
            return candidate_role
        
        if current_role and not current_is_placeholder:
            logger.debug(f"  决策: [优先级3b] 当前角色是有效的非中文名/占位符，而候选是无效的。保留当前角色。")
            logger.debug(f"  选择: '{current_role}'")
            logger.debug(f"--- [角色选择结束] ---")
            return current_role

        # 优先级 4: 处理占位符。如果两者之一是占位符，则返回一个（候选优先）。
        if candidate_role: # 如果候选有内容（此时只能是占位符）
            logger.debug(f"  决策: [优先级4a] 候选角色是占位符。选择候选角色。")
            logger.debug(f"  选择: '{candidate_role}'")
            logger.debug(f"--- [角色选择结束] ---")
            return candidate_role
            
        if current_role: # 如果当前有内容（此时只能是占位符）
            logger.debug(f"  决策: [优先级4b] 当前角色是占位符，候选为空。保留当前角色。")
            logger.debug(f"  选择: '{current_role}'")
            logger.debug(f"--- [角色选择结束] ---")
            return current_role

        # 优先级 5: 所有情况都处理完，只剩下两者都为空。
        logger.debug(f"  决策: [优先级5] 所有输入均为空或无效。返回空字符串。")
        logger.debug(f"  选择: ''")
        logger.debug(f"--- [角色选择结束] ---")
        return ""
    # ✨✨✨从豆瓣API获取指定媒体的演员原始数据列表✨✨✨
    def _fetch_douban_cast(self, media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从豆瓣API获取演员原始数据。"""
        # 假设 constants 和 self.douban_api 已经存在
        # if not (getattr(constants, 'DOUBAN_API_AVAILABLE', False) and self.douban_api and \
        #         self.data_source_mode in [constants.DOMESTIC_SOURCE_MODE_LOCAL_THEN_ONLINE, constants.DOMESTIC_SOURCE_MODE_ONLINE_ONLY]):
        #     return []
        
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
    # ✨✨✨格式化从豆瓣获取的原始演员数据，进行初步清理和去重，使其符合内部处理格式✨✨✨
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
    # ✨✨✨为给定的候选人（通常来自豆瓣）查询并返回其在TMDb和IMDb上的ID✨✨✨
    def _fetch_external_ids_for_person(self, person_candidate: Dict[str, Any], media_info: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
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
    # ✨✨✨处理单个媒体项目演员列表的核心方法✨✨✨
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
                provider_ids_lower = {k.lower(): v for k, v in provider_ids.items()}

                # ★★★ 新增的“身份补全”逻辑从这里开始 ★★★
                tmdb_id = provider_ids_lower.get("tmdb")
                if not tmdb_id:
                    logger.debug(f"演员 '{emby_name}' (EmbyID: {emby_pid}) 缺少TMDb ID，尝试通过API补全...")
                    details = emby_handler.get_emby_item_details(
                        item_id=emby_pid, # ★★★ 精准修正 ★★★
                        emby_server_url=self.emby_url,
                        emby_api_key=self.emby_api_key,
                        user_id=self.emby_user_id
                    )
                    if details:
                        full_provider_ids = details.get("ProviderIds", {})
                        full_provider_ids_lower = {k.lower(): v for k, v in full_provider_ids.items()}
                        tmdb_id = full_provider_ids_lower.get("tmdb")
                        if tmdb_id:
                            logger.info(f"  -> 成功为 '{emby_name}' 补全了TMDb ID: {tmdb_id}")
                            # 把补全的ID更新回 provider_ids，以便后续使用
                            provider_ids['Tmdb'] = tmdb_id 
                # ★★★ “身份补全”逻辑结束 ★★★
                actor_internal_format = {
                    "Name": emby_name,
                    "OriginalName": person_emby.get("OriginalName", emby_name),
                    "Role": str(person_emby.get("Role", "")).strip(),
                    "Type": "Actor",
                    "EmbyPersonId": emby_pid,
                    # ★★★ 使用我们补全后的 provider_ids ★★★
                    "TmdbPersonId": str(provider_ids.get("Tmdb", "") or provider_ids.get("tmdb", "")).strip() or None,
                    "DoubanCelebrityId": str(provider_ids.get("Douban", "") or provider_ids.get("douban", "")).strip() or None,
                    "ImdbId": str(provider_ids.get("Imdb", "") or provider_ids.get("imdb", "")).strip() or None,
                    "ProviderIds": provider_ids.copy(),
                    "Order": person_emby.get("Order"),
                    "_source_comment": "from_emby_initial"
                }
                final_cast_list.append(actor_internal_format)
                # 使用 Emby 的原始数据更新映射表
                self.actor_db_manager.upsert_person(
                    cursor,
                    {
                        "emby_id": actor_internal_format["EmbyPersonId"],
                        "name": actor_internal_format["Name"],
                        "tmdb_id": actor_internal_format["TmdbPersonId"],
                        "douban_id": actor_internal_format["DoubanCelebrityId"],
                        "imdb_id": actor_internal_format["ImdbId"]
                    },
                )

            logger.info(f"步骤 1: 基准列表创建完成，包含 {len(final_cast_list)} 位演员。")
            if self.is_stop_requested():
                conn.commit()
                return final_cast_list

            # ======================================================================
            # 步骤 2: 从豆瓣获取数据并与基准列表比对 (丰富现有演员)
            # ======================================================================
            logger.info("步骤 2: 获取豆瓣演员并与基准列表比对...")
            formatted_douban_candidates = []
            if media_type_for_log in ["Movie", "Series"]:
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
                        
                        # 【错误修复】修正此处的逻辑
                        if not is_match:
                            # 直接在 if 条件中使用 utils.are_names_match，因为它返回布尔值
                            if utils.are_names_match(
                                douban_candidate.get("Name"), douban_candidate.get("OriginalName"),
                                emby_actor_to_update.get("Name"), emby_actor_to_update.get("OriginalName")
                            ):
                                # 如果匹配成功，再设置 is_match 和 match_reason
                                is_match = True
                                match_reason = f"名字匹配 ('{douban_candidate.get('Name')}' vs '{emby_actor_to_update.get('Name')}')"

                        if is_match:
                            logger.info(f"  匹配成功: 豆瓣候选 '{douban_candidate.get('Name')}' 通过 [{match_reason}] 关联到 Emby 演员 '{emby_actor_to_update.get('Name')}'")
                            
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
                                                        
                            # 更新映射表
                            self.actor_db_manager.upsert_person(
                                cursor,
                                {
                                    "emby_id": emby_actor_to_update["EmbyPersonId"],
                                    "name": emby_actor_to_update["Name"], # 使用更新后的名字
                                    "douban_id": emby_actor_to_update.get("DoubanCelebrityId"),
                                    # douban_name_override 的逻辑被简化，因为 upsert 会处理
                                },
                            )

                            matched_douban_indices.add(i)
                            match_found = True
                            break
                    
                    if not match_found:
                        unmatched_douban_candidates.append(douban_candidate)
            
            logger.info(f"步骤 2: 完成。{len(matched_douban_indices) if formatted_douban_candidates else 0} 位豆瓣演员已匹配，{len(unmatched_douban_candidates)} 位溢出。")
            if self.is_stop_requested():
                conn.commit()
                return final_cast_list

            # ======================================================================
            # 步骤 3: 条件化处理流程 (核心逻辑分叉点)
            # ======================================================================
            limit = self.config.get(constants.CONFIG_OPTION_MAX_ACTORS_TO_PROCESS, 30)
            try:
                limit = int(limit)
                if limit <= 0: limit = 30
            except (ValueError, TypeError):
                limit = 30
            
            current_actor_count = len(final_cast_list)

            if current_actor_count >= limit:
                logger.info(f"当前演员数 ({current_actor_count}) 已达上限 ({limit})，将跳过所有新增演员的流程。")
                if unmatched_douban_candidates:
                    discarded_names = [d.get('Name') for d in unmatched_douban_candidates]
                    logger.info(f"--- 因此，将丢弃 {len(discarded_names)} 位未能匹配的豆瓣演员: {', '.join(discarded_names[:5])}{'...' if len(discarded_names) > 5 else ''} ---")
            else:
                logger.info(f"当前演员数 ({current_actor_count}) 低于上限 ({limit})，进入补充模式，继续处理 {len(unmatched_douban_candidates)} 位溢出的豆瓣演员。")
                
                if self.tmdb_api_key and unmatched_douban_candidates:
                    # ... (这部分逻辑保持不变) ...
                    for douban_candidate in unmatched_douban_candidates:
                        if self.is_stop_requested(): break
                        
                        tmdb_id, imdb_id, _ = self._fetch_external_ids_for_person(douban_candidate, media_info)
                        
                        match_found = False
                        if tmdb_id or imdb_id:
                            for emby_actor_to_update in final_cast_list:
                                is_match, match_reason = False, ""
                                if imdb_id and imdb_id == emby_actor_to_update.get("ImdbId"):
                                    is_match, match_reason = True, f"IMDb ID ({imdb_id})"
                                elif tmdb_id and tmdb_id == emby_actor_to_update.get("TmdbPersonId"):
                                    is_match, match_reason = True, f"TMDb ID ({tmdb_id})"

                                if is_match:
                                    logger.info(f"  交叉匹配成功: 豆瓣候选 '{douban_candidate.get('Name')}' 通过 [{match_reason}] 再次关联到 Emby 演员 '{emby_actor_to_update.get('Name')}'")
                                    # ★★★ 核心修复：在更新数据库前，先把所有新ID都补充到 emby_actor_to_update 字典里 ★★★
                                    if tmdb_id:
                                        emby_actor_to_update["TmdbPersonId"] = tmdb_id
                                    if imdb_id:
                                        emby_actor_to_update["ImdbId"] = imdb_id
                                    if douban_candidate.get("DoubanCelebrityId"):
                                        emby_actor_to_update["DoubanCelebrityId"] = douban_candidate.get("DoubanCelebrityId")
                                    
                                    # 现在，emby_actor_to_update 身上带齐了所有证件！
                                    
                                    # ★★★ 更新映射表 ★★★
                                    self.actor_db_manager.upsert_person(
                                        cursor,
                                        {
                                            "emby_id": emby_actor_to_update["EmbyPersonId"],
                                            "name": emby_actor_to_update["Name"],
                                            "tmdb_id": emby_actor_to_update.get("TmdbPersonId"),
                                            "imdb_id": emby_actor_to_update.get("ImdbId"),
                                            "douban_id": emby_actor_to_update.get("DoubanCelebrityId"),
                                        },
                                    )
                                    match_found = True
                                    break
                        
                        if not match_found:
                            logger.info(f"  丢弃: 豆瓣候选 '{douban_candidate.get('Name')}' (D:{douban_candidate.get('DoubanCelebrityId')}, T:{tmdb_id}, I:{imdb_id}) 未能匹配任何基准演员，将被丢弃。")

            logger.info("步骤 3: 条件化处理完成。")
            if self.is_stop_requested():
                conn.commit()
                return final_cast_list
            
            
            # ======================================================================
            # 步骤 4: 最终截断
            # ======================================================================
            original_count = len(final_cast_list)
            if original_count > limit:
                logger.info(f"演员列表总数 ({original_count}) 超过上限 ({limit})，将在翻译前进行截断。")
                final_cast_list.sort(key=lambda x: x.get('Order') if x.get('Order') is not None and x.get('Order') >= 0 else 999)
                final_cast_list = final_cast_list[:limit]
                logger.info(f"截断后，剩余 {len(final_cast_list)} 位演员进入最终处理。")

            # ======================================================================
            # 步骤 5: 对最终演员表进行翻译和格式化
            # ======================================================================
            logger.info(f"步骤 5: 对最终 {len(final_cast_list)} 位演员进行翻译和格式化...")
            
            ai_translation_succeeded = False

            if self.ai_translation_enabled and self.ai_translator:
                logger.info("AI翻译已启用，优先尝试批量翻译模式。")
                texts_to_translate = set()
                translation_cache = {} # 用于存储从数据库或API获取的翻译结果

                # 1. 收集所有需要翻译的文本，并优先使用数据库缓存
                for actor in final_cast_list:
                    for field_key in ["Name", "Role"]:
                        original_text = actor.get(field_key)
                        if field_key == 'Role':
                            original_text = utils.clean_character_name_static(original_text)
                        
                        if not original_text or not original_text.strip() or utils.contains_chinese(original_text):
                            continue

                        cached_entry = DoubanApi._get_translation_from_db(original_text, cursor=cursor)
                        if cached_entry and cached_entry.get("translated_text"):
                            translation_cache[original_text] = cached_entry.get("translated_text")
                        else:
                            texts_to_translate.add(original_text)
                
                # 2. 如果有需要翻译的文本，则调用批量API
                if texts_to_translate:
                    logger.info(f"共收集到 {len(texts_to_translate)} 个独立词条需要通过AI翻译。")
                    try:
                        translation_map = self.ai_translator.batch_translate(list(texts_to_translate))
                        
                        if translation_map:
                            logger.info(f"AI批量翻译成功，返回 {len(translation_map)} 个结果。")
                            translation_cache.update(translation_map)
                            
                            # 将新翻译的结果存入数据库缓存
                            for original, translated in translation_map.items():
                                DoubanApi._save_translation_to_db(original, translated, self.ai_translator.provider, cursor=cursor)
                            
                            ai_translation_succeeded = True
                        else:
                            logger.warning("AI批量翻译调用成功，但未返回任何翻译结果。可能是API内部错误（如余额不足）。将降级到传统翻译引擎。")

                    except Exception as e:
                        logger.error(f"调用AI批量翻译时发生严重错误: {e}。将降级到传统翻译引擎。", exc_info=True)
                else:
                    logger.info("所有词条均在缓存中找到，无需AI翻译。")
                    ai_translation_succeeded = True

                # 3. 如果AI成功（无论通过API还是缓存），则回填结果
                if ai_translation_succeeded:
                    for actor_data in final_cast_list:
                        # 更新名字
                        original_name = actor_data.get("Name")
                        if original_name in translation_cache:
                            actor_data["Name"] = translation_cache[original_name]
                        
                        # 更新角色
                        original_role = utils.clean_character_name_static(actor_data.get("Role"))
                        if original_role in translation_cache:
                            actor_data["Role"] = translation_cache[original_role]
                        else:
                            actor_data["Role"] = original_role

            # ★★★ 降级逻辑 ★★★
            if not ai_translation_succeeded:
                if self.config.get("ai_translation_enabled", False):
                    logger.info("AI翻译失败，正在启动降级程序，使用传统翻译引擎...")
                else:
                    logger.info("AI翻译未启用，使用传统翻译引擎（如果配置了）。")
                
                # 使用健壮的逐个翻译逻辑作为回退
                for actor_data in final_cast_list:
                    if self.is_stop_requested(): break
                    
                    current_name = actor_data.get("Name")
                    actor_data["Name"] = self._translate_actor_field(current_name, "演员名", current_name, db_cursor_for_cache=cursor)

                    role_cleaned = utils.clean_character_name_static(actor_data.get("Role"))
                    actor_data["Role"] = self._translate_actor_field(role_cleaned, "角色名", actor_data.get("Name"), db_cursor_for_cache=cursor)

            # 翻译完成后，进行统一的格式化处理
            is_animation = "Animation" in media_info.get("Genres", []) or "动画" in media_info.get("Genres", [])
            for actor_data in final_cast_list:
                final_role = actor_data.get("Role", "")

                # 移除中文角色名中的所有空格
                if final_role and utils.contains_chinese(final_role):
                    final_role = final_role.replace(" ", "").replace("　", "")
                
                if is_animation:
                    final_role = f"{final_role} (配音)" if final_role and not final_role.endswith("(配音)") else "配音"
                elif not final_role:
                    final_role = "演员"
                
                if final_role != actor_data.get("Role"):
                    logger.debug(f"  角色名格式化: '{actor_data.get('Role')}' -> '{final_role}' (演员: {actor_data.get('Name')})")
                    actor_data["Role"] = final_role

            logger.info("步骤 5: 完成。")
            
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
    # ✨✨✨处理单个媒体项目（电影或剧集）的入口方法✨✨✨
    def process_single_item(self, emby_item_id: str, force_reprocess_this_item: bool = False, process_episodes: bool = True) -> bool:
        if self.is_stop_requested():
            return False

        if not force_reprocess_this_item and emby_item_id in self.processed_items_cache:
            logger.info(f"媒体 '{self.processed_items_cache.get(emby_item_id, emby_item_id)}' 已处理过，跳过。")
            return True

        try:
            item_details = emby_handler.get_emby_item_details(emby_item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
            if not item_details:
                self.save_to_failed_log(emby_item_id, f"未知项目(ID:{emby_item_id})", "无法获取Emby项目详情")
                return False
        except Exception as e:
            self.save_to_failed_log(emby_item_id, f"未知项目(ID:{emby_item_id})", f"获取Emby详情异常: {e}")
            return False

        item_name_for_log = item_details.get("Name", f"ID:{emby_item_id}")
        
        try:
            # ★★★ 核心：直接调用我们新的、统一的流程函数 ★★★
            # 并将 process_episodes 参数传递下去
            self._process_item_api_mode(item_details, process_episodes)
            
            return True
        except InterruptedError:
            logger.info(f"处理 '{item_name_for_log}' 的过程中被用户中止。")
            return False
        except Exception as e:
            logger.error(f"处理 '{item_name_for_log}' 时发生严重错误: {e}", exc_info=True)
            self.save_to_failed_log(emby_item_id, item_name_for_log, f"核心处理异常: {str(e)}", item_details.get("Type"))
            return False
    # # ✨✨✨处理单个媒体项（电影、剧集或单集）的核心业务逻辑✨✨✨
    def _process_item_core_logic(self, item_details: Dict[str, Any], force_reprocess_this_item: bool = False) -> bool:
        """
        最终版：只负责处理，不再关心是否跳过。
        """
        item_id = item_details.get("Id")
        if not item_id:
            return False
        
        # ✨ 这里的跳过检查是针对分集的，保留它是对的，但可以简化日志 ✨
        if not force_reprocess_this_item and item_id in self.processed_items_cache:
            logger.debug(f"核心逻辑：项目ID '{item_id}' 已在缓存中，跳过。")
            return True

        item_type_for_log = item_details.get("Type", "未知类型")
        item_name_for_log = item_details.get("Name", f"未知项目(ID:{item_id})")
        
        current_emby_cast_raw = item_details.get("People", [])
        original_emby_cast_count = len(current_emby_cast_raw)
        logger.info(f"媒体 '{item_name_for_log}' 原始Emby People数量: {original_emby_cast_count}")

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
            if item_type_for_log in ["Movie", "Series"]:
                logger.info(f"正在为 {item_type_for_log} '{item_name_for_log}' 进行质量评估...")
                processing_score = self._evaluate_cast_processing_quality(final_cast_for_item, original_emby_cast_count)
                
                MIN_SCORE_FOR_REVIEW = float(self.config.get("min_score_for_review", 6.0))
                if processing_score < MIN_SCORE_FOR_REVIEW:
                    self.save_to_failed_log(item_id, item_name_for_log, f"处理评分过低({processing_score:.1f})", item_type_for_log, score=processing_score)
                else:
                    self._remove_from_failed_log_if_exists(item_id)
                
                self.save_to_processed_log(item_id, item_name_for_log, score=processing_score)
            
            elif item_type_for_log == "Episode":
                self.processed_items_cache.add(item_id)

            return True
        else:
            if item_type_for_log in ["Movie", "Series"]:
                self.save_to_failed_log(item_id, item_name_for_log, "更新Emby演员信息失败", item_type_for_log, score=None)
            return False
    # ✨✨✨使用手动编辑的演员列表来处理单个媒体项目✨✨✨    
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
        logger.info("手动处理：开始前置翻译演员名字...")
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
        logger.info("手动处理：演员名字前置翻译完成。")

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
    # ✨✨✨如果一个项目本次处理成功且评分达标，则从失败日志中移除它✨✨✨    
    def _remove_from_failed_log_if_exists(self, item_id: str):
        """如果 item_id 存在于 failed_log 中，则删除它。"""
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM failed_log WHERE item_id = ?", (item_id,))
            if cursor.rowcount > 0:
                logger.info(f"Item ID '{item_id}' 已从【待复核列表】中移除。")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"从 failed_log 删除 Item ID '{item_id}' 时失败: {e}", exc_info=True)
    # ✨✨✨将从 TMDb API 获取的演员数据格式化为 Emby Handler 或内部处理所需的标准字典格式✨✨✨
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
    # ✨✨✨处理配置文件中指定的所有媒体库的入口方法✨✨✨
    def process_full_library(self, update_status_callback: Optional[callable] = None, force_reprocess_all: bool = False, process_episodes: bool = True):
        self.clear_stop_signal()
        logger.debug("process_full_library: 方法开始执行。")
        logger.debug(f"  force_reprocess_all: {force_reprocess_all}, process_episodes: {process_episodes}")

        if force_reprocess_all:
            logger.info("用户请求强制重处理所有媒体项，将清除数据库中的已处理记录。")
            self.clear_processed_log()

        if not all([self.emby_url, self.emby_api_key, self.emby_user_id]):
            logger.error("Emby配置不完整，无法处理整个媒体库。")
            if update_status_callback:
                update_status_callback(-1, "Emby配置不完整")
            return

        current_libs_to_process = self.libraries_to_process
        if not current_libs_to_process:
            logger.warning("配置中要处理的媒体库ID列表为空，无需处理。")
            if update_status_callback:
                update_status_callback(100, "未在配置中指定要处理的媒体库。")
            return

        logger.info(f"开始全量处理选定的Emby媒体库 (ID(s): {current_libs_to_process})...")
        
        movies = emby_handler.get_emby_library_items(self.emby_url, self.emby_api_key, "Movie", self.emby_user_id, library_ids=current_libs_to_process) or []
        if self.is_stop_requested(): return

        series_list = emby_handler.get_emby_library_items(self.emby_url, self.emby_api_key, "Series", self.emby_user_id, library_ids=current_libs_to_process) or []
        if self.is_stop_requested(): return

        all_items = movies + series_list
        total_items = len(all_items)

        if total_items == 0:
            logger.info("从选定的媒体库中未获取到任何电影或剧集项目，处理结束。")
            if update_status_callback:
                update_status_callback(100, "未在选定库中找到项目。")
            return

        logger.info(f"总共获取到 {len(movies)} 部电影和 {len(series_list)} 部剧集，共 {total_items} 个项目待处理。")

        for i, item in enumerate(all_items):
            if self.is_stop_requested():
                logger.info("全量媒体库处理在项目迭代中被用户中断。")
                break

            item_id = item.get('Id')
            if not item_id:
                logger.warning(f"条目缺少ID，跳过: {item.get('Name')}")
                continue

            progress_percent = int(((i + 1) / total_items) * 100)
            message = f"正在处理 ({i+1}/{total_items}): {item.get('Name')}"
            logger.info(f"--- {message} ---")
            if update_status_callback:
                update_status_callback(progress_percent, message)

            # ✨ 只保留这一个干净的调用 ✨
            self.process_single_item(
                item_id, 
                force_reprocess_this_item=force_reprocess_all,
                process_episodes=process_episodes
            )

            delay = float(self.config.get("delay_between_items_sec", 0.5))
            if delay > 0 and i < total_items - 1:
                time.sleep(delay)

        if not self.is_stop_requested():
            logger.info("全量处理Emby媒体库结束。")
            if update_status_callback:
                update_status_callback(100, "全量处理完成。")
    # ✨✨✨关闭 MediaProcessorAPI 实例，释放其占用的所有资源（如数据库连接、API会话等）✨✨✨
    def close(self):
        """关闭 MediaProcessorAPI 实例，例如关闭数据库连接池或释放其他资源。"""
        if self.douban_api and hasattr(self.douban_api, 'close'):
            logger.debug("正在关闭 MediaProcessorAPI 中的 DoubanApi session...")
            self.douban_api.close()
        # 如果有其他需要关闭的资源，例如数据库连接池（如果使用的话），在这里关闭
        logger.debug("MediaProcessorAPI close 方法执行完毕。")
    # ✨✨✨使用从网页提取的新演员列表来“丰富”当前演员列表✨✨✨
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
    # ✨✨✨辅助函数更新演员映射表✨✨✨
    # ✨✨✨一键翻译演员列表✨✨✨
    def translate_cast_list(self, cast_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        【批量优化版】翻译演员列表中的演员名和角色名，不执行任何其他操作。
        """
        if not cast_list:
            return []

        logger.info(f"一键翻译：开始批量处理 {len(cast_list)} 位演员的姓名和角色。")
        translated_cast = [dict(actor) for actor in cast_list]
        
        ai_translation_succeeded = False

        # 优先尝试AI批量翻译
        if self.ai_translator and self.config.get("ai_translation_enabled", False):
            texts_to_translate = set()
            
            # 1. 收集所有需要翻译的文本
            for actor in translated_cast:
                for field_key in ['name', 'role']:
                    text = actor.get(field_key, '').strip()
                    if text and not utils.contains_chinese(text):
                        texts_to_translate.add(text)
            
            # 2. 如果有需要翻译的文本，则调用批量API
            if texts_to_translate:
                logger.info(f"一键翻译：收集到 {len(texts_to_translate)} 个词条需要AI翻译。")
                try:
                    translation_map = self.ai_translator.batch_translate(list(texts_to_translate))
                    if translation_map:
                        logger.info(f"一键翻译：AI批量翻译成功，返回 {len(translation_map)} 个结果。")
                        
                        # 3. 回填翻译结果
                        for i, actor in enumerate(translated_cast):
                            # 更新演员名
                            original_name = actor.get('name', '').strip()
                            if original_name in translation_map:
                                translated_cast[i]['name'] = translation_map[original_name]
                            
                            # 更新角色名
                            original_role = actor.get('role', '').strip()
                            if original_role in translation_map:
                                translated_cast[i]['role'] = translation_map[original_role]
                            
                            # 只要有任何一项被翻译，就更新状态
                            if translated_cast[i].get('name') != actor.get('name') or translated_cast[i].get('role') != actor.get('role'):
                                translated_cast[i]['matchStatus'] = '已翻译'
                        
                        ai_translation_succeeded = True
                    else:
                        logger.warning("一键翻译：AI批量翻译未返回结果，将降级。")
                except Exception as e:
                    logger.error(f"一键翻译：调用AI批量翻译时出错: {e}，将降级。", exc_info=True)

        # 如果AI翻译未启用或失败，则降级到传统引擎
        if not ai_translation_succeeded:
            if self.config.get("ai_translation_enabled", False):
                logger.info("一键翻译：AI翻译失败，降级到传统引擎逐个翻译。")
            else:
                logger.info("一键翻译：AI未启用，使用传统引擎逐个翻译。")
                
            conn = self._get_db_connection()
            try:
                cursor = conn.cursor()
                for i, actor in enumerate(translated_cast):
                    actor_name_for_log = actor.get('name', '未知演员')
                    
                    # 翻译演员名
                    name_to_translate = actor.get('name', '').strip()
                    if name_to_translate and not utils.contains_chinese(name_to_translate):
                        translated_name = self._translate_actor_field(name_to_translate, "演员名(一键翻译)", name_to_translate, cursor)
                        if translated_name and translated_name != name_to_translate:
                            translated_cast[i]['name'] = translated_name
                            actor_name_for_log = translated_name

                    # 翻译角色名
                    role_to_translate = actor.get('role', '').strip()
                    if role_to_translate and not utils.contains_chinese(role_to_translate):
                        translated_role = self._translate_actor_field(role_to_translate, "角色名(一键翻译)", actor_name_for_log, cursor)
                        if translated_role and translated_role != role_to_translate:
                            translated_cast[i]['role'] = translated_role

                    if translated_cast[i].get('name') != actor.get('name') or translated_cast[i].get('role') != actor.get('role'):
                        translated_cast[i]['matchStatus'] = '已翻译'

                conn.commit()
            except Exception as e:
                logger.error(f"一键翻译（降级模式）时发生错误: {e}", exc_info=True)
                if conn: conn.rollback()
            finally:
                if conn: conn.close()

        logger.info("一键翻译完成。")
        return translated_cast
    # ✨✨✨批量翻译辅助方法✨✨✨
    def _batch_translate_actor_fields_ai(self, cast_list: List[Dict[str, Any]], db_cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
        """
        使用AI批量翻译演员列表中的姓名和角色。
        """
        logger.info("  (AI批量模式) 开始收集需要翻译的字段...")
        
        texts_to_translate = set()
        translation_cache = {} # 用于存储从数据库或API获取的翻译结果

        # 步骤 1: 收集所有需要翻译的文本，并优先使用数据库缓存
        for actor in cast_list:
            for field in ["Name", "Role"]:
                original_text = actor.get(field)
                if not original_text or not original_text.strip() or utils.contains_chinese(original_text):
                    continue

                # 检查数据库缓存
                cached_entry = DoubanApi._get_translation_from_db(original_text)
                if cached_entry and cached_entry.get("translated_text"):
                    cached_translation = cached_entry.get("translated_text")
                    engine_used = cached_entry.get("engine_used")
                    logger.debug(f"    数据库翻译缓存命中 for '{original_text}' -> '{cached_translation}' (引擎: {engine_used})")
                    translation_cache[original_text] = cached_translation
                else:
                    # 如果缓存未命中，则加入待翻译集合
                    texts_to_translate.add(original_text)

        # 步骤 2: 如果有需要翻译的文本，则进行一次性批量API调用
        if texts_to_translate:
            logger.info(f"  (AI批量模式) 收集到 {len(texts_to_translate)} 个独立词条需要通过API翻译。")
            
            # 调用AITranslator的批量翻译方法
            # 注意：你需要确保你的 AITranslator 类有 batch_translate 方法
            try:
                # 将 set 转换为 list
                api_results = self.ai_translator.batch_translate(list(texts_to_translate))
                
                # 更新我们的翻译缓存，并存入数据库
                if api_results:
                    logger.info(f"  (AI批量模式) API成功返回 {len(api_results)} 个翻译结果。")
                    translation_cache.update(api_results)
                    
                    # 将新翻译的结果存入数据库缓存
                    for original, translated in api_results.items():
                        DoubanApi._save_translation_to_db(
                            original, 
                            translated, 
                            self.ai_translator.provider, 
                            cursor=db_cursor
                        )
                else:
                    logger.warning("  (AI批量模式) AI批量翻译API没有返回有效结果。")

            except Exception as e:
                logger.error(f"  (AI批量模式) 调用AI批量翻译API时发生错误: {e}", exc_info=True)
        else:
            logger.info("  (AI批量模式) 所有需要翻译的字段均在数据库缓存中找到，无需调用API。")

        # 步骤 3: 映射回填，使用完整的 translation_cache 更新演员列表
        logger.info("  (AI批量模式) 开始将翻译结果回填到演员列表...")
        for actor in cast_list:
            # 翻译名字
            original_name = actor.get("Name")
            if original_name in translation_cache:
                actor["Name"] = translation_cache[original_name]
            
            # 翻译角色 (先清理)
            original_role = utils.clean_character_name_static(actor.get("Role"))
            if original_role in translation_cache:
                actor["Role"] = translation_cache[original_role] # 更新时使用已翻译的结果
            else:
                actor["Role"] = original_role # 即使没翻译，也要用清理后的结果

        return cast_list

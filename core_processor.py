# core_processor_sa.py

import os
import json
import sqlite3
from typing import Dict, List, Optional, Any, Tuple
import shutil
import threading
import time
import requests
import copy
import random
# 确保所有依赖都已正确导入
import emby_handler
import tmdb_handler
import utils
import constants
import logging
import actor_utils
from actor_utils import ActorDBManager
from actor_utils import get_db_connection as get_central_db_connection
from ai_translator import AITranslator
from utils import LogDBManager, get_override_path_for_item
from watchlist_processor import WatchlistProcessor
from douban import DoubanApi
logger = logging.getLogger(__name__)
try:
    from douban import DoubanApi
    DOUBAN_API_AVAILABLE = True
except ImportError:
    DOUBAN_API_AVAILABLE = False
    class DoubanApi:
        def __init__(self, *args, **kwargs): pass
        def get_acting(self, *args, **kwargs): return {}
        def close(self): pass
        @staticmethod
        def _get_translation_from_db(*args, **kwargs): return None
        @staticmethod
        def _save_translation_to_db(*args, **kwargs): pass

def _read_local_json(file_path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(file_path):
        logger.warning(f"本地元数据文件不存在: {file_path}")
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取本地JSON文件失败: {file_path}, 错误: {e}")
        return None

class MediaProcessor:
    def __init__(self, config: Dict[str, Any]):
        # ★★★ 然后，从这个 config 字典里，解析出所有需要的属性 ★★★
        self.config = config
        self.db_path = config.get('db_path')
        if not self.db_path:
            raise ValueError("数据库路径 (db_path) 未在配置中提供。")

        # 初始化我们的数据库管理员
        self.actor_db_manager = ActorDBManager(self.db_path)
        self.log_db_manager = LogDBManager(self.db_path)

        # 从 config 中获取所有其他配置
        self.douban_api = None
        if getattr(constants, 'DOUBAN_API_AVAILABLE', False):
            try:
                # --- ✨✨✨ 核心修改区域 START ✨✨✨ ---

                # 1. 从配置中获取冷却时间 (这部分逻辑您可能已经有了)
                douban_cooldown = self.config.get(constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN, 2.0)
                
                # 2. 从配置中获取 Cookie，使用我们刚刚在 constants.py 中定义的常量
                douban_cookie = self.config.get(constants.CONFIG_OPTION_DOUBAN_COOKIE, "")
                
                # 3. 添加一个日志，方便调试
                if not douban_cookie:
                    logger.debug(f"配置文件中未找到或未设置 '{constants.CONFIG_OPTION_DOUBAN_COOKIE}'。如果豆瓣API返回'need_login'错误，请在此处配置。")
                else:
                    logger.debug("已从配置中加载豆瓣 Cookie。")

                # 4. 将所有参数传递给 DoubanApi 的构造函数
                self.douban_api = DoubanApi(
                    db_path=self.db_path,
                    cooldown_seconds=douban_cooldown,
                    user_cookie=douban_cookie  # <--- 将 cookie 传进去
                )
                logger.debug("DoubanApi 实例已在 MediaProcessorAPI 中创建。")
                
                # --- ✨✨✨ 核心修改区域 END ✨✨✨ ---

            except Exception as e:
                logger.error(f"MediaProcessorAPI 初始化 DoubanApi 失败: {e}", exc_info=True)
        else:
            logger.warning("DoubanApi 常量指示不可用，将不使用豆瓣功能。")
        self.emby_url = self.config.get("emby_server_url")
        self.emby_api_key = self.config.get("emby_api_key")
        self.emby_user_id = self.config.get("emby_user_id")
        self.tmdb_api_key = self.config.get("tmdb_api_key", "")
        self.local_data_path = self.config.get("local_data_path", "").strip()
        self.sync_images_enabled = self.config.get(constants.CONFIG_OPTION_SYNC_IMAGES, False)
        self.translator_engines = self.config.get(constants.CONFIG_OPTION_TRANSLATOR_ENGINES, constants.DEFAULT_TRANSLATOR_ENGINES_ORDER)
        
        self.ai_enabled = self.config.get("ai_translation_enabled", False)
        self.ai_translator = AITranslator(self.config) if self.ai_enabled else None
        
        self._stop_event = threading.Event()
        self.processed_items_cache = self._load_processed_log_from_db()
        self.manual_edit_cache = {}
        logger.debug("(神医模式)初始化完成。")
    # --- 清除已处理记录 ---
    def clear_processed_log(self):
        """
        【已改造】清除数据库和内存中的已处理记录。
        使用中央数据库连接函数。
        """
        try:
            # 1. ★★★ 调用中央函数，并传入 self.db_path ★★★
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                
                logger.debug("正在从数据库删除 processed_log 表中的所有记录...")
                cursor.execute("DELETE FROM processed_log")
                # with 语句会自动处理 conn.commit()
            
            logger.info("数据库中的已处理记录已清除。")

            # 2. 清空内存缓存
            self.processed_items_cache.clear()
            logger.info("内存中的已处理记录缓存已清除。")

        except Exception as e:
            logger.error(f"清除数据库或内存已处理记录时失败: {e}", exc_info=True)
            # 3. ★★★ 重新抛出异常，通知上游调用者操作失败 ★★★
            raise
    # ★★★ 公开的、独立的追剧判断方法 ★★★
    def check_and_add_to_watchlist(self, item_details: Dict[str, Any]):
        """
        检查一个媒体项目是否为剧集，如果是，则执行智能追剧判断并添加到待看列表。
        此方法被设计为由外部事件（如Webhook）显式调用。
        """
        item_name_for_log = item_details.get("Name", f"未知项目(ID:{item_details.get('Id')})")
        
        if item_details.get("Type") != "Series":
            # 如果不是剧集，直接返回，不打印非必要的日志
            return

        logger.info(f"Webhook触发：开始为新入库剧集 '{item_name_for_log}' 进行追剧状态判断...")
        try:
            # 实例化 WatchlistProcessor 并执行添加操作
            watchlist_proc = WatchlistProcessor(self.config)
            watchlist_proc.add_series_to_watchlist(item_details)
        except Exception as e_watchlist:
            logger.error(f"在自动添加 '{item_name_for_log}' 到追剧列表时发生错误: {e_watchlist}", exc_info=True)

    def signal_stop(self):
        self._stop_event.set()

    def clear_stop_signal(self):
        self._stop_event.clear()

    def get_stop_event(self) -> threading.Event:
        """返回内部的停止事件对象，以便传递给其他函数。"""
        return self._stop_event

    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def _load_processed_log_from_db(self) -> Dict[str, str]:
        log_dict = {}
        try:
            # 1. ★★★ 使用 with 语句和中央函数 ★★★
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 2. 执行查询
                cursor.execute("SELECT item_id, item_name FROM processed_log")
                rows = cursor.fetchall()
                
                # 3. 处理结果
                for row in rows:
                    if row['item_id'] and row['item_name']:
                        log_dict[row['item_id']] = row['item_name']
            
            # 4. with 语句会自动处理所有事情，代码干净利落！

        except Exception as e:
            # 5. ★★★ 记录更详细的异常信息 ★★★
            logger.error(f"从数据库读取已处理记录失败: {e}", exc_info=True)
        return log_dict

    # ✨ 从 SyncHandler 迁移并改造，用于在本地缓存中查找豆瓣JSON文件
    def _find_local_douban_json(self, imdb_id: Optional[str], douban_id: Optional[str], douban_cache_dir: str) -> Optional[str]:
        """根据 IMDb ID 或 豆瓣 ID 在本地缓存目录中查找对应的豆瓣JSON文件。"""
        if not os.path.exists(douban_cache_dir):
            return None
        
        # 优先使用 IMDb ID 匹配，更准确
        if imdb_id:
            for dirname in os.listdir(douban_cache_dir):
                if dirname.startswith('0_'): continue
                if imdb_id in dirname:
                    dir_path = os.path.join(douban_cache_dir, dirname)
                    for filename in os.listdir(dir_path):
                        if filename.endswith('.json'):
                            return os.path.join(dir_path, filename)
                            
        # 其次使用豆瓣 ID 匹配
        if douban_id:
            for dirname in os.listdir(douban_cache_dir):
                if dirname.startswith(f"{douban_id}_"):
                    dir_path = os.path.join(douban_cache_dir, dirname)
                    for filename in os.listdir(dir_path):
                        if filename.endswith('.json'):
                            return os.path.join(dir_path, filename)
        return None

    # ✨ 封装了“优先本地缓存，失败则在线获取”的逻辑
    def _get_douban_cast_with_local_cache(self, media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        获取豆瓣演员列表。优先从本地 douban-movies/tv 缓存中查找，如果找不到，再通过在线API获取。
        """
        # 1. 准备查找所需的信息
        provider_ids = media_info.get("ProviderIds", {})
        imdb_id = provider_ids.get("Imdb")
        douban_id = provider_ids.get("Douban")
        item_type = media_info.get("Type")
        
        douban_cache_dir_name = "douban-movies" if item_type == "Movie" else "douban-tv"
        douban_cache_path = os.path.join(self.local_data_path, "cache", douban_cache_dir_name)

        # 2. 尝试从本地缓存查找
        local_json_path = self._find_local_douban_json(imdb_id, douban_id, douban_cache_path)

        if local_json_path:
            logger.debug(f"发现本地豆瓣缓存文件，将直接使用: {local_json_path}")
            douban_data = _read_local_json(local_json_path)
            # 注意：豆瓣刮削器缓存的键是 'actors'
            if douban_data and 'actors' in douban_data:
                # 为了与API返回的格式兼容，这里做个转换
                # API返回: {'cast': [...]}  本地缓存: {'actors': [...]}
                return douban_data.get('actors', [])
            else:
                logger.warning(f"本地豆瓣缓存文件 '{local_json_path}' 无效或不含 'actors' 键，将回退到在线API。")
        
        # 3. 如果本地未找到，回退到在线API
        logger.info("未找到本地豆瓣缓存，将通过在线API获取演员信息。")
        return actor_utils.find_douban_cast(self.douban_api, media_info)
    # --- 通过豆瓣ID查找映射表 ---
    def _find_person_in_map_by_douban_id(self, douban_id: str, cursor: sqlite3.Cursor) -> Optional[sqlite3.Row]:
        """
        根据豆瓣名人ID在 person_identity_map 表中查找对应的记录。
        """
        if not douban_id:
            return None
        try:
            cursor.execute(
                "SELECT * FROM person_identity_map WHERE douban_celebrity_id = ?",
                (douban_id,)
            )
            return cursor.fetchone()
        except sqlite3.Error as e:
            logger.error(f"通过豆瓣ID '{douban_id}' 查询 person_identity_map 时出错: {e}")
            return None
    # --- 通过ImbdID查找映射表 ---
    def _find_person_in_map_by_imdb_id(self, imdb_id: str, cursor: sqlite3.Cursor) -> Optional[sqlite3.Row]:
        """
        根据 IMDb ID 在 person_identity_map 表中查找对应的记录。
        """
        if not imdb_id:
            return None
        try:
            # 核心改动：将查询字段从 douban_celebrity_id 改为 imdb_id
            cursor.execute(
                "SELECT * FROM person_identity_map WHERE imdb_id = ?",
                (imdb_id,)
            )
            return cursor.fetchone()
        except sqlite3.Error as e:
            logger.error(f"通过 IMDb ID '{imdb_id}' 查询 person_identity_map 时出错: {e}")
            return None
    # --- 核心处理流程 ---
    def _process_cast_list_from_local(self, local_cast_list: List[Dict[str, Any]], item_details_from_emby: Dict[str, Any], cursor: sqlite3.Cursor, tmdb_api_key: Optional[str], stop_event: Optional[threading.Event]) -> List[Dict[str, Any]]:
        """
        【V10 - 批量翻译优化版】使用本地缓存或在线API获取豆瓣演员，再进行匹配和处理。
        """
        douban_candidates_raw = self._get_douban_cast_with_local_cache(item_details_from_emby)
        douban_candidates = actor_utils.format_douban_cast(douban_candidates_raw)
        
        # 这从根本上杜绝了后续所有因“二次匹配”导致的数据覆盖和排序问题。
        final_cast_map = {str(actor['id']): actor for actor in local_cast_list if actor.get('id')}
    
        # 保留对无ID演员的 fallback 处理，以防万一
        for actor in local_cast_list:
            if not actor.get('id'):
                # 使用名字作为 key，确保它不会覆盖有ID的演员
                name_key = f"name_{str(actor.get('name', '')).lower()}"
                if name_key not in final_cast_map:
                    final_cast_map[name_key] = actor

        unmatched_douban_candidates = []
        matched_douban_indices = set()
        # --- 步骤 1: 用豆瓣演员名和原始演员表匹配 ---
        logger.debug("--- 匹配阶段 1: 按名字匹配 ---")
        matched_douban_indices = set()
        for i, d_actor in enumerate(douban_candidates):
            # ✨✨✨ 严格使用 format_douban_cast 输出的标准键名 (大写开头) ✨✨✨
            douban_name_zh = d_actor.get("Name")
            douban_name_en = d_actor.get("OriginalName")
            douban_id = d_actor.get("DoubanCelebrityId")

            for l_actor in final_cast_map.values():
        
                # ✨✨✨ 统一使用经过验证的、简单直接的匹配逻辑 ✨✨✨
                is_match, match_reason = False, ""
        
                # --- 引擎1: 简单直接的精确匹配 (高优先级) ---
                dc_name_lower = str(d_actor.get("Name") or "").lower().strip()
                dc_orig_name_lower = str(d_actor.get("OriginalName") or "").lower().strip()
                local_name_lower = str(l_actor.get("name") or "").lower().strip()
                local_original_name_lower = str(l_actor.get("original_name") or "").lower().strip()

                if dc_name_lower and (dc_name_lower == local_name_lower or dc_name_lower == local_original_name_lower):
                    is_match, match_reason = True, f"精确匹配 (豆瓣中文名)"
                elif dc_orig_name_lower and (dc_orig_name_lower == local_name_lower or dc_orig_name_lower == local_original_name_lower):
                    is_match, match_reason = True, f"精确匹配 (豆瓣外文名)"


                if is_match:
                    logger.info(f"  匹配成功 ({match_reason}): 豆瓣演员 '{d_actor.get('Name')}' -> 本地演员 '{l_actor.get('name')}'")
                    
                    # 匹配成功后的逻辑 (保持不变)
                    l_actor["name"] = d_actor.get("Name")
                    cleaned_douban_character = utils.clean_character_name_static(d_actor.get("Role"))
                    l_actor["character"] = actor_utils.select_best_role(
                        l_actor.get("character"), 
                        cleaned_douban_character 
                    )
                    if d_actor.get("DoubanCelebrityId"): 
                        l_actor["douban_id"] = d_actor.get("DoubanCelebrityId")
                    
                    matched_douban_indices.add(i)
                    break # 找到匹配就跳出内层循环
        
        unmatched_douban_candidates = [d for i, d in enumerate(douban_candidates) if i not in matched_douban_indices]
        
        # --- ★★★ V-Final 核心修改：条件化处理流程 ★★★ ---
        limit = self.config.get(constants.CONFIG_OPTION_MAX_ACTORS_TO_PROCESS, 30)
        try:
            limit = int(limit)
            if limit <= 0: limit = 30
        except (ValueError, TypeError):
            limit = 30

        current_actor_count = len(final_cast_map)

        if current_actor_count >= limit:
            logger.info(f"当前演员数 ({current_actor_count}) 已达上限 ({limit})，跳过所有新增演员的流程。")
        else:
            logger.info(f"当前演员数 ({current_actor_count}) 低于上限 ({limit})，进入补充模式（继续新增）。")
            if final_cast_map:
                max_order = max(actor.get('order', -1) for actor in final_cast_map.values() if actor.get('order') is not None)
                next_order_index = max_order + 1
            else:
                next_order_index = 0
            logger.debug(f"--- 匹配阶段 2: 用豆瓣ID查 person_identity_map ({len(unmatched_douban_candidates)} 位演员) ---")
            unmatched_douban_candidates = [d for i, d in enumerate(douban_candidates) if i not in matched_douban_indices]
            still_unmatched = []
            for d_actor in unmatched_douban_candidates:
                if self.is_stop_requested(): raise InterruptedError("任务中止")
                d_douban_id = d_actor.get("DoubanCelebrityId")
                match_found = False
                if d_douban_id:
                    entry = self._find_person_in_map_by_douban_id(d_douban_id, cursor)
                    if entry and entry["tmdb_person_id"]:
                        tmdb_id_from_map = entry["tmdb_person_id"]
                        if tmdb_id_from_map not in final_cast_map:
                            logger.info(f"  新增成功 (通过 豆瓣ID映射): 豆瓣演员 '{d_actor.get('Name')}' -> 新增 TMDbID: {tmdb_id_from_map}")
                            new_actor_entry = {
                                "id": tmdb_id_from_map, "name": d_actor.get("Name"), "original_name": d_actor.get("OriginalName"),
                                "character": d_actor.get("Role"), "adult": False, "gender": 0, "known_for_department": "Acting",
                                "popularity": 0.0, "profile_path": None, "cast_id": None, "credit_id": None, "order": -1,
                                "order": next_order_index,
                                "imdb_id": entry["imdb_id"], "douban_id": d_douban_id, "_is_newly_added": True
                            }
                            final_cast_map[tmdb_id_from_map] = new_actor_entry
                        match_found = True
                if not match_found:
                    still_unmatched.append(d_actor)
            unmatched_douban_candidates = still_unmatched

            # --- 步骤 3 & 4: 查询IMDbID -> TMDb反查 -> 新增 ---
            logger.debug(f"--- 匹配阶段 3 & 4: 用IMDb ID进行最终匹配和新增 ({len(unmatched_douban_candidates)} 位演员) ---")
            still_unmatched_final = []
            for d_actor in unmatched_douban_candidates:
                if self.is_stop_requested(): raise InterruptedError("任务中止")
                # ✨ 核心修改：在每次循环开始时检查上限
                if len(final_cast_map) >= limit:
                    logger.info(f"演员数已达上限 ({limit})，跳过剩余 {len(unmatched_douban_candidates) - i} 位演员的API查询。")
                    # 将所有剩下的演员直接加入 still_unmatched_final
                    still_unmatched_final.extend(unmatched_douban_candidates[i:])
                    break # 彻底结束新增流程
                d_douban_id = d_actor.get("DoubanCelebrityId")
                match_found = False
                if d_douban_id and self.douban_api and self.tmdb_api_key:
                    if self.is_stop_requested():
                        logger.info("任务在处理豆瓣演员时被中止 (豆瓣API调用前)。")
                        raise InterruptedError("任务中止")
                    details = self.douban_api.celebrity_details(d_douban_id)
                    time.sleep(0.3)
                    
                    d_imdb_id = None
                    if details and not details.get("error"):
                        try:
                            info_list = details.get("extra", {}).get("info", [])
                            if isinstance(info_list, list):
                                for item in info_list:
                                    if isinstance(item, list) and len(item) == 2 and item[0] == 'IMDb编号':
                                        d_imdb_id = item[1]
                                        break
                        except Exception as e_parse:
                            logger.warning(f"    -> 解析 IMDb ID 时发生意外错误: {e_parse}")
                    
                    if d_imdb_id:
                        logger.debug(f"    -> 为 '{d_actor.get('Name')}' 获取到 IMDb ID: {d_imdb_id}，开始匹配...")
                        
                        # ✨✨✨ 1. 优先调用新的辅助函数查询本地数据库 ✨✨✨
                        entry_from_map = self._find_person_in_map_by_imdb_id(d_imdb_id, cursor)
                        
                        if entry_from_map and entry_from_map["tmdb_person_id"]:
                            tmdb_id_from_map = str(entry_from_map["tmdb_person_id"])
                            
                            if tmdb_id_from_map not in final_cast_map:
                                logger.info(f"  新增成功 (通过 IMDb映射): 豆瓣演员 '{d_actor.get('Name')}' -> 新增 TMDbID: {tmdb_id_from_map}")
                                new_actor_entry = {
                                    "id": tmdb_id_from_map, "name": d_actor.get("Name"), "original_name": d_actor.get("OriginalName"),
                                    "character": d_actor.get("Role"), "adult": False, "gender": 0, "known_for_department": "Acting",
                                    "popularity": 0.0, "profile_path": None, "cast_id": None, "credit_id": None, "order": -1,
                                    "order": next_order_index,
                                    "imdb_id": d_imdb_id, "douban_id": d_douban_id, "_is_newly_added": True
                                }
                                final_cast_map[tmdb_id_from_map] = new_actor_entry
                            match_found = True

                        # ✨✨✨ 2. 如果数据库未命中，才去调用在线 API ✨✨✨
                        if not match_found:
                            logger.debug(f"    -> 数据库未找到 {d_imdb_id} 的映射，开始通过 TMDb API 反查...")
                            if self.is_stop_requested():
                                logger.info("任务在处理豆瓣演员时被中止 (TMDb API调用前)。")
                                raise InterruptedError("任务中止")
                            
                            person_from_tmdb = tmdb_handler.find_person_by_external_id(d_imdb_id, self.tmdb_api_key, "imdb_id")
                            if person_from_tmdb and person_from_tmdb.get("id"):
                                tmdb_id_from_find = str(person_from_tmdb.get("id"))
                                
                                if tmdb_id_from_find not in final_cast_map:
                                    logger.info(f"  新增成功 (通过 TMDb反查): 豆瓣演员 '{d_actor.get('Name')}' -> 新增 TMDbID: {tmdb_id_from_find}")
                                    new_actor_entry = {
                                        "id": tmdb_id_from_find, "name": d_actor.get("Name"), "original_name": d_actor.get("OriginalName"),
                                        "character": d_actor.get("Role"), "adult": False, "gender": 0, "known_for_department": "Acting",
                                        "popularity": 0.0, "profile_path": None, "cast_id": None, "credit_id": None, "order": -1,
                                        "order": next_order_index,
                                        "imdb_id": d_imdb_id, "douban_id": d_douban_id, "_is_newly_added": True
                                    }
                                    final_cast_map[tmdb_id_from_find] = new_actor_entry
                                    
                                    # ✨✨✨ 3. 重要：将新获取的映射关系存回数据库！✨✨✨
                                    self.actor_db_manager.upsert_person(
                                        cursor,
                                        {
                                            "tmdb_id": tmdb_id_from_find,
                                            "imdb_id": d_imdb_id,
                                            "douban_id": d_douban_id,
                                            "name": d_actor.get("Name")
                                        }
                                    )
                                match_found = True
                if not match_found:
                    still_unmatched_final.append(d_actor)

            if still_unmatched_final:
                discarded_names = [d.get('Name') for d in still_unmatched_final]
                logger.info(f"--- 最终丢弃 {len(still_unmatched_final)} 位无匹配的豆瓣演员 ---")


        intermediate_cast_list = list(final_cast_map.values())
        # ★★★ 在截断前进行一次全量反哺 ★★★
        logger.debug(f"截断前：将 {len(intermediate_cast_list)} 位演员的完整映射关系反哺到数据库...")
        for actor_data in intermediate_cast_list:
            self.actor_db_manager.upsert_person(
                cursor,
                {
                    "tmdb_id": actor_data.get("id"),
                    "name": actor_data.get("name"),
                    "imdb_id": actor_data.get("imdb_id"),
                    "douban_id": actor_data.get("douban_id"),
                },
            )
        logger.info("所有演员的ID映射关系已保存。")
        # 【★★★ 终极安检门：在函数出口强制去重 ★★★】
        logger.debug("执行最终出口检查，强制移除所有同名演员...")
        final_unique_list = []
        seen_final_names = set()
        
        # 先按 order 排序，确保我们保留的是最重要的那个
        intermediate_cast_list.sort(key=lambda x: x.get('order') if x.get('order') is not None else 999)
        
        for actor in intermediate_cast_list:
            actor_name = actor.get('name')
            if actor_name not in seen_final_names:
                final_unique_list.append(actor)
                seen_final_names.add(actor_name)
            else:
                logger.warning(f"出口安检：发现并拦截了重复的演员 '{actor_name}'。")
                
        # 用绝对干净的列表进行后续的截断和翻译
        cast_to_process = final_unique_list
        # 步骤 演员列表截断 (先截断！)
        # ======================================================================
        max_actors = self.config.get(constants.CONFIG_OPTION_MAX_ACTORS_TO_PROCESS, 30)
        try:
            limit = int(max_actors)
            if limit <= 0: limit = 30
        except (ValueError, TypeError):
            limit = 30
        
        original_count = len(intermediate_cast_list)
        if original_count > limit:
            logger.info(f"演员列表总数 ({original_count}) 超过上限 ({limit})，将在翻译前进行截断。")
            intermediate_cast_list.sort(key=lambda x: x.get('order') if x.get('order') is not None and x.get('order') >= 0 else 999)
            cast_to_process = intermediate_cast_list[:limit]
        else:
            cast_to_process = intermediate_cast_list
        
        logger.info(f"将对 {len(cast_to_process)} 位演员进行最终的翻译和格式化处理...")

        # ======================================================================
        # 步骤 B: 翻译准备与执行 (后收集，并检查缓存！)
        # ======================================================================
        ai_translation_succeeded = False

        if self.ai_translator and self.config.get("ai_translation_enabled", False):
            logger.info("AI翻译已启用，优先尝试批量翻译模式。")
            
            texts_to_translate = set()
            translation_cache = {} # 用于存储从DB或API获取的翻译

            # ✨✨✨ 核心修复 1: 在截断后的列表上收集，并检查缓存 ✨✨✨
            logger.info(f"开始从 {len(cast_to_process)} 位演员中收集需要翻译的词条...")
            for actor in cast_to_process:
                for field_key in ['name', 'character']:
                    text = actor.get(field_key)
                    if field_key == 'character':
                        text = utils.clean_character_name_static(text)
                    
                    if not text or not text.strip() or utils.contains_chinese(text):
                        continue

                    # ✨✨✨ 核心修复 2: 强制检查缓存 ✨✨✨
                    cached_entry = DoubanApi._get_translation_from_db(text, cursor=cursor)
                    if cached_entry and cached_entry.get("translated_text"):
                        translation_cache[text] = cached_entry.get("translated_text")
                    elif cached_entry:
                        pass # 缓存中有失败记录，不翻译
                    else:
                        texts_to_translate.add(text) # 缓存未命中，加入待翻译列表
            
            if texts_to_translate:
                logger.info(f"共收集到 {len(texts_to_translate)} 个独立词条需要通过AI翻译。")
                try:
                    # 【★★★ 升级调用方式 ★★★】
                    # 1. 从 item_details_from_emby 中获取上下文信息
                    item_title = item_details_from_emby.get("Name")
                    item_year = item_details_from_emby.get("ProductionYear")

                    # 2. 在调用 batch_translate 时传入上下文
                    translation_map_from_api = self.ai_translator.batch_translate(
                        texts=list(texts_to_translate),
                        title=item_title,
                        year=item_year
                    )
                    
                    if translation_map_from_api:
                        logger.info(f"AI批量翻译成功，返回 {len(translation_map_from_api)} 个结果。")
                        translation_cache.update(translation_map_from_api)
                        for original, translated in translation_map_from_api.items():
                            DoubanApi._save_translation_to_db(original, translated, self.ai_translator.provider, cursor=cursor)
                        
                        ai_translation_succeeded = True
                    else:
                        logger.warning("AI批量翻译调用成功，但未返回任何翻译结果。")
                except Exception as e:
                    logger.error(f"调用AI批量翻译时发生严重错误: {e}", exc_info=True)
            else:
                logger.info("所有需要翻译的词条均在数据库缓存中找到，无需调用API。")
                ai_translation_succeeded = True

            if ai_translation_succeeded:
                for actor in cast_to_process:
                    original_name = actor.get('name')
                    if original_name in translation_cache:
                        actor['name'] = translation_cache[original_name]
                    
                    original_character = utils.clean_character_name_static(actor.get('character'))
                    if original_character in translation_cache:
                        actor['character'] = translation_cache[original_character]
                    else:
                        actor['character'] = original_character

        # ★★★ 降级逻辑 ★★★
        # 如果AI翻译未启用，或者尝试了但失败了，则执行传统翻译
        if not ai_translation_succeeded:
            if self.config.get("ai_translation_enabled", False):
                logger.info("AI翻译失败，正在启动降级程序，使用传统翻译引擎...")
            else:
                logger.info("AI翻译未启用，使用传统翻译引擎（如果配置了）。")

            # ✨✨✨ 核心修改在这里 ✨✨✨
            # 1. 在循环外准备好所有需要的参数，避免重复获取
            translator_engines_order = self.config.get("translator_engines_order", [])
            ai_enabled_flag = self.config.get("ai_translation_enabled", False)
            
            # 使用你原来的、健壮的逐个翻译逻辑作为回退
            for actor in cast_to_process:
                if self.is_stop_requested():
                    raise InterruptedError("任务在翻译演员列表时被中止")
                
                # 2. 使用新的、正确的参数列表来调用函数
                actor['name'] = actor_utils.translate_actor_field(
                    text=actor.get('name'),
                    db_cursor=cursor,
                    ai_translator=self.ai_translator,
                    translator_engines=translator_engines_order,
                    ai_enabled=ai_enabled_flag
                )
                
                cleaned_character = utils.clean_character_name_static(actor.get('character'))
                
                actor['character'] = actor_utils.translate_actor_field(
                    text=cleaned_character,
                    db_cursor=cursor,
                    ai_translator=self.ai_translator,
                    translator_engines=translator_engines_order,
                    ai_enabled=ai_enabled_flag
                )
            # ✨✨✨ 修改结束 ✨✨✨

        # 返回处理完的、已经截断和翻译的列表
        return cast_to_process
        
    # ✨✨✨API中文化演员表✨✨✨
    def _process_api_track_person_names_only(self, 
                                         people_list: List[Dict[str, Any]], 
                                         cursor: sqlite3.Cursor, 
                                         item_details_from_emby: Dict[str, Any]):
        """
        【API轨道 - 批量翻译重构版】
        此函数负责将指定媒体项目中演员的英文名批量翻译成中文，并更新回Emby。
        """
        item_id = item_details_from_emby.get("Id")
        item_name_for_log = item_details_from_emby.get("Name", f"未知媒体(ID:{item_id})")
        logger.info(f"前置翻译开始为 '{item_name_for_log}' 进行演员名批量中文化...")

        # 1. 从 Emby 获取原始演员列表
        original_cast = item_details_from_emby.get("People", [])
        if not original_cast:
            logger.info("前置翻译：该媒体在Emby中没有演员信息，跳过。")
            return

        # 检查 AI 翻译器和配置是否就绪
        if not self.ai_translator or not self.config.get("ai_translation_enabled", False):
            logger.warning("前置翻译：AI翻译器未配置或未启用，跳过演员名中文化。")
            return

        try:
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()

                # --- ★★★ 开始移植批量翻译逻辑 ★★★ ---

                # 2. 收集所有需要翻译的演员名
                texts_to_translate = set()
                # 同时，我们创建一个从 Emby Person ID 到原始名字的映射，方便后续更新
                person_id_to_name_map = {} 

                for person in original_cast:
                    emby_person_id = person.get("Id")
                    current_name = person.get("Name")

                    if not emby_person_id or not current_name:
                        continue
                    
                    person_id_to_name_map[emby_person_id] = current_name
                    
                    # 检查数据库缓存，如果缓存未命中且需要翻译，则加入集合
                    cached_entry = DoubanApi._get_translation_from_db(current_name, cursor=cursor)
                    if not cached_entry and not utils.contains_chinese(current_name):
                        texts_to_translate.add(current_name)

                # 3. 如果有需要翻译的文本，则调用批量API
                if texts_to_translate:
                    logger.info(f"前置翻译：发现 {len(texts_to_translate)} 个需要翻译的演员名。")
                    try:
                        # 【★★★ 升级调用方式 ★★★】
                        # 1. 从 item_details_from_emby 中获取上下文信息
                        item_title = item_details_from_emby.get("Name")
                        item_year = item_details_from_emby.get("ProductionYear")

                        # 2. 在调用 batch_translate 时传入上下文
                        translation_map = self.ai_translator.batch_translate(
                            texts=list(texts_to_translate),
                            title=item_title,
                            year=item_year
                        )

                        if translation_map:
                            logger.info(f"AI批量翻译成功，返回 {len(translation_map)} 个结果。")
                            
                            # 将新翻译的结果存入数据库缓存
                            for original, translated in translation_map.items():
                                DoubanApi._save_translation_to_db(original, translated, self.ai_translator.provider, cursor=cursor)
                            
                            # 4. ★★★ 核心：遍历原始映射，执行Emby更新 ★★★
                            logger.info("开始将翻译结果更新回 Emby...")
                            for person_id, original_name in person_id_to_name_map.items():
                                if self.is_stop_requested():
                                    logger.info("前置翻译：更新Emby时任务被中止。")
                                    break

                                # 在翻译结果中查找这个演员的译名
                                translated_name = translation_map.get(original_name)
                                
                                # 如果找到了翻译结果，就更新 Emby
                                if translated_name:
                                    logger.info(f"更新演员译名: '{original_name}' -> '{translated_name}' (Emby Person ID: {person_id})")
                                    emby_handler.update_person_details(
                                        person_id=person_id,
                                        new_data={"Name": translated_name},
                                        emby_server_url=self.emby_url,
                                        emby_api_key=self.emby_api_key,
                                        user_id=self.emby_user_id
                                    )
                                    time.sleep(0.2) # 增加微小延迟
                        else:
                            logger.warning("AI批量翻译调用成功，但未返回任何翻译结果。可能是API内部错误。")

                    except Exception as e:
                        logger.error(f"调用AI批量翻译时发生严重错误: {e}。", exc_info=True)
                else:
                    logger.info("前置翻译：所有演员名均无需翻译（已是中文或缓存命中）。")

        except Exception as e:
            logger.error(f"前置翻译在为 '{item_name_for_log}' 处理演员中文化时发生严重错误: {e}", exc_info=True)
        
        logger.info(f"前置翻译为 '{item_name_for_log}' 的演员中文化处理完成。")

    def _process_item_core_logic(self, item_details_from_emby: Dict[str, Any], force_reprocess_this_item: bool, should_process_episodes_this_run: bool, force_fetch_from_tmdb: bool):
        """
        【V-Final 升级版 - 支持强制在线获取】
        在一个统一的数据库事务中，串行执行所有数据库相关的处理轨道。
        增加了 force_fetch_from_tmdb 标志以实现逻辑分岔。
        """
        # --- 准备工作 (确保所有变量先定义) ---
        item_id = item_details_from_emby.get("Id")
        item_name_for_log = item_details_from_emby.get("Name", f"未知项目(ID:{item_id})")
        tmdb_id = item_details_from_emby.get("ProviderIds", {}).get("Tmdb")
        item_type = item_details_from_emby.get("Type")
        log_prefix = f"[{'在线模式' if force_fetch_from_tmdb else '本地模式'}]"
        
        # ★★★ 现在 item_name_for_log 已经定义好了 ★★★
        original_emby_actor_count = len(item_details_from_emby.get("People", []))
        
        # ★★★ 在这里使用它就完全安全了 ★★★
        logger.debug(f"记录到 '{item_name_for_log}' 在Emby中的原始演员数为: {original_emby_actor_count}")

        logger.debug(f"{log_prefix} --- 开始核心处理: '{item_name_for_log}' (TMDbID: {tmdb_id}) ---")

        if self.is_stop_requested():
            logger.info(f"{log_prefix} 任务在处理 '{item_name_for_log}' 前被中止。")
            return False

        # ★★★ 使用 with 语句来管理数据库连接和事务 ★★★
        try:
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                # ★★★ 轨道一：API 轨道 (仅中文化演员名) ★★★
                if not force_fetch_from_tmdb:
                    # 【★★★ 修复点 1：传递上下文 ★★★】
                    self._process_api_track_person_names_only(
                        people_list=item_details_from_emby.get("People", []), 
                        cursor=cursor,
                        item_details_from_emby=item_details_from_emby
                    )
                
                if self.is_stop_requested(): raise InterruptedError("任务被中止")

                # ★★★ 轨道二：JSON 轨道 (神医模式核心) ★★★
                if not tmdb_id or not self.local_data_path:
                    error_msg = "缺少TMDbID" if not tmdb_id else "未配置本地数据路径"
                    logger.warning(f"【JSON轨道】跳过处理 '{item_name_for_log}'，原因: {error_msg}。")
                    conn.commit()
                    return False
                
                cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
                base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
                base_override_dir = os.path.join(self.local_data_path, "override", cache_folder_name, tmdb_id)
                image_override_dir = os.path.join(base_override_dir, "images")
                os.makedirs(base_override_dir, exist_ok=True)
                os.makedirs(image_override_dir, exist_ok=True)
                base_json_filename = "all.json" if item_type == "Movie" else "series.json"
                
                # =================================================================
                # 【★★★ 逻辑分岔点 ★★★】
                # =================================================================
                base_json_data_original = None
                # 1. 先决策：确定最终要执行的模式
                should_fetch_online = force_fetch_from_tmdb
                json_file_path = os.path.join(base_cache_dir, base_json_filename)

                if not should_fetch_online and not os.path.exists(json_file_path):
                    logger.warning(f"本地元数据文件不存在: {json_file_path}。将自动切换到在线模式进行获取。")
                    should_fetch_online = True # 强制切换

                # 2. 后定调：根据最终决策，生成本次处理的日志前缀
                mode_str = "本地模式"
                if should_fetch_online:
                    # 如果最初不是强制在线，说明是自动切换的
                    mode_str = "在线模式(自动切换)" if not force_fetch_from_tmdb else "在线模式"
                log_prefix = f"[{mode_str}]"

                # 3. 再执行：使用正确的日志前缀开始记录和处理
                logger.info(f"{log_prefix} 开始处理JSON元数据并生成到覆盖缓存目录 ---")
                
                base_json_data_original = None
                if should_fetch_online:
                    # 【在线路径】
                    logger.debug(f"{log_prefix} 开始从TMDb在线获取元数据。")
                    base_json_data_original = self._fetch_and_build_tmdb_base_json(
                        tmdb_id=tmdb_id, 
                        item_type=item_type, 
                        item_name=item_name_for_log
                    )
                else:
                    # 【本地路径】
                    logger.debug(f"{log_prefix} 从本地缓存文件读取元数据: {json_file_path}")
                    base_json_data_original = _read_local_json(json_file_path)

                if not base_json_data_original:
                    raise ValueError(f"无法获取基础JSON数据 (模式: {'在线' if force_fetch_from_tmdb else '本地'})")
                
                # --- 阶段1: 演员处理 ---
                full_tmdb_cast_as_base = []
                if item_type == "Movie":
                    full_tmdb_cast_as_base = base_json_data_original.get("credits", {}).get("cast", []) or base_json_data_original.get("casts", {}).get("cast", []) or []
                elif item_type == "Series":
                    if should_fetch_online:
                         full_tmdb_cast_as_base = base_json_data_original.get("credits", {}).get("cast", []) or base_json_data_original.get("casts", {}).get("cast", []) or []
                    else:
                         full_tmdb_cast_as_base = self._get_full_tv_cast_from_cache(base_cache_dir)
                
                initial_actor_count = len(full_tmdb_cast_as_base)
                logger.info(f"{log_prefix} 成功获取了 {initial_actor_count} 位基准演员。")
                
                intermediate_cast = self._process_cast_list_from_local(
                    full_tmdb_cast_as_base,
                    item_details_from_emby, 
                    cursor,
                    self.tmdb_api_key,
                    self.get_stop_event()
                )
                
                genres = item_details_from_emby.get("Genres", [])
                is_animation = "Animation" in genres or "动画" in genres
                
                final_cast_perfect = actor_utils.format_and_complete_cast_list(intermediate_cast, is_animation, self.config)
        
                # --- 阶段2: 文件写入 ---
                base_json_data_for_override = copy.deepcopy(base_json_data_original)
                if item_type == "Movie":
                    base_json_data_for_override.setdefault("casts", {})["cast"] = final_cast_perfect
                else:
                    base_json_data_for_override.setdefault("credits", {})["cast"] = final_cast_perfect
                
                override_json_path = os.path.join(base_override_dir, base_json_filename)
                temp_json_path = f"{override_json_path}.{random.randint(1000, 9999)}.tmp"
                try:
                    with open(temp_json_path, 'w', encoding='utf-8') as f:
                        json.dump(base_json_data_for_override, f, ensure_ascii=False, indent=4)
                    os.replace(temp_json_path, override_json_path)
                    logger.info(f"✅ 成功生成覆盖元数据文件: {override_json_path}")
                except Exception as e_write:
                    logger.error(f"写入或重命名元数据文件时发生错误: {e_write}", exc_info=True)
                    if os.path.exists(temp_json_path): os.remove(temp_json_path)
                    raise e_write

                # 【处理分集】
                if item_type == "Series" and should_process_episodes_this_run:
                    logger.info(f"{log_prefix} 开始为 '{item_name_for_log}' 的所有分集创建/覆盖元数据...")
                    
                    # 1. 从 Emby 获取真实的季结构，这是决定我们要创建哪些文件的唯一依据。
                    children = emby_handler.get_series_children(
                        item_id, 
                        self.emby_url, 
                        self.emby_api_key, 
                        self.emby_user_id, 
                        series_name_for_log=item_name_for_log
                    ) or []
                    
                    if not children:
                        logger.warning(f"未能从Emby获取到 '{item_name_for_log}' 的任何季/集信息，跳过分集处理。")
                    else:
                        season_files_to_process = set()
                        
                        # 2. 根据Emby的子项，构建出我们需要处理的季文件名列表。
                        for child in children:
                            season_number = None
                            if child.get("Type") == "Season":
                                season_number = child.get("IndexNumber")
                            elif child.get("Type") == "Episode":
                                season_number = child.get("ParentIndexNumber")
                            
                            if season_number is not None:
                                season_files_to_process.add(f"season-{season_number}.json")
                        
                        if not season_files_to_process:
                             logger.info("在Emby子项中未找到有效的季信息，不处理分集文件。")
                        else:
                            logger.info(f"根据Emby结构，将为 {len(season_files_to_process)} 个季文件创建/覆盖元数据。")

                            # 3. 为每一个需要处理的季文件，生成一个最小化的、但包含完美演员表的覆盖JSON。
                            for filename in season_files_to_process:
                                # 我们不关心任何原始文件是否存在，我们只管创建/覆盖。
                                override_content = {
                                    "credits": {
                                        "cast": final_cast_perfect # <-- 使用我们流程中生成的、最完美的演员表
                                    }
                                    # 注意：这里只包含了演员表。神医插件在读取时，
                                    # 会将这个文件的内容与从TMDb获取的其他分集信息进行合并。
                                    # 这确保了我们只覆盖我们想覆盖的部分。
                                }
                                
                                override_child_path = os.path.join(base_override_dir, filename)
                                try:
                                    # 使用原子写入，确保文件写入的完整性
                                    temp_child_path = f"{override_child_path}.{random.randint(1000, 9999)}.tmp"
                                    with open(temp_child_path, 'w', encoding='utf-8') as f:
                                        json.dump(override_content, f, ensure_ascii=False, indent=4)
                                    os.replace(temp_child_path, override_child_path)
                                    logger.debug(f"成功创建/覆盖分集元数据: {filename}")
                                except Exception as e:
                                    logger.error(f"写入分集覆盖JSON失败: {override_child_path}, {e}")
                                    if os.path.exists(temp_child_path): 
                                        os.remove(temp_child_path)


                # 【同步图片】
                if self.sync_images_enabled:
                    if self.is_stop_requested(): raise InterruptedError("任务被中止")
                    logger.info(f"{log_prefix} 开始为 '{item_name_for_log}' 下载图片...")
                    image_map = {"Primary": "poster.jpg", "Backdrop": "fanart.jpg", "Logo": "clearlogo.png"}
                    if item_type == "Movie": image_map["Thumb"] = "landscape.jpg"
                    for image_type, filename in image_map.items():
                        emby_handler.download_emby_image(item_id, image_type, os.path.join(image_override_dir, filename), self.emby_url, self.emby_api_key)
                    
                    if item_type == "Series" and should_process_episodes_this_run:
                        children = emby_handler.get_series_children(item_id, self.emby_url, self.emby_api_key, self.emby_user_id, series_name_for_log=item_name_for_log) or []
                        for child in children:
                            child_type, child_id = child.get("Type"), child.get("Id")
                            if child_type == "Season":
                                season_number = child.get("IndexNumber")
                                if season_number is not None:
                                    emby_handler.download_emby_image(child_id, "Primary", os.path.join(image_override_dir, f"season-{season_number}.jpg"), self.emby_url, self.emby_api_key)
                            elif child_type == "Episode":
                                season_number, episode_number = child.get("ParentIndexNumber"), child.get("IndexNumber")
                                if season_number is not None and episode_number is not None:
                                    emby_handler.download_emby_image(child_id, "Primary", os.path.join(image_override_dir, f"season-{season_number}-episode-{episode_number}.jpg"), self.emby_url, self.emby_api_key)

                # --- 阶段3: 统计、评分和最终日志记录 ---
                final_actor_count = len(final_cast_perfect)
                logger.info(f"✨✨✨处理统计 '{item_name_for_log}'✨✨✨")
                logger.info(f"  - 原有演员: {original_emby_actor_count} 位")
                newly_added_count = final_actor_count - original_emby_actor_count
                if newly_added_count > 0:
                    logger.info(f"  - 新增演员: {newly_added_count} 位") # 可以换个说法
                logger.info(f"  - 最终演员: {final_actor_count} 位")

                if self.is_stop_requested(): raise InterruptedError("任务被中止")

                processing_score = actor_utils.evaluate_cast_processing_quality(
                    final_cast=final_cast_perfect,
                    original_cast_count=original_emby_actor_count,
                    expected_final_count=len(final_cast_perfect),
                    is_animation=is_animation
                )
                min_score_for_review = float(self.config.get("min_score_for_review", constants.DEFAULT_MIN_SCORE_FOR_REVIEW))
                
                refresh_success = emby_handler.refresh_emby_item_metadata(
                    item_emby_id=item_id,
                    emby_server_url=self.emby_url,
                    emby_api_key=self.emby_api_key,
                    replace_all_metadata_param=True,
                    item_name_for_log=item_name_for_log
                )
                
                if not refresh_success:
                    raise RuntimeError("触发Emby刷新失败")
                
                if processing_score < min_score_for_review:
                    self.log_db_manager.save_to_failed_log(
                        cursor, item_id, item_name_for_log, 
                        f"处理评分过低 ({processing_score:.1f} / {min_score_for_review:.1f})", 
                        item_type, score=processing_score
                    )
                else:
                    self.log_db_manager.save_to_processed_log(cursor, item_id, item_name_for_log, score=processing_score)
                    self.log_db_manager.remove_from_failed_log(cursor, item_id)
                
                logger.info(f"✨✨✨处理完成 '{item_name_for_log}'✨✨✨")
                conn.commit()
                return True

        except (ValueError, InterruptedError) as e:
            # 捕获我们预期的、不应中止整个程序的错误
            log_message = f"处理 '{item_name_for_log}' 的过程中被用户中止" if isinstance(e, InterruptedError) else f"处理 '{item_name_for_log}' 失败: {e}"
            logger.warning(f"{log_message}")
            # 注意：因为异常发生在 with 块内，事务会自动回滚，我们无需手动操作
            # 我们可以在这里手动写入失败日志，但这需要一个新的数据库连接
            # 更简单的做法是，让上层调用者决定是否记录失败
            return False
    
        except Exception as outer_e:
            # 捕获所有其他意外的、严重的错误
            logger.error(f"核心处理流程中发生未知严重错误 for '{item_name_for_log}': {outer_e}", exc_info=True)
            # 事务同样会自动回滚
            # 我们可以在这里手动写入失败日志
            try:
                with get_central_db_connection(self.db_path) as conn_fail:
                    self.log_db_manager.save_to_failed_log(conn_fail.cursor(), item_id, item_name_for_log, f"核心处理异常: {str(outer_e)}", item_type)
            except Exception as log_e:
                logger.error(f"写入失败日志时再次发生错误: {log_e}")
            return False

    def process_single_item(self, emby_item_id: str, force_reprocess_this_item: bool = False, process_episodes: Optional[bool] = None, force_fetch_from_tmdb: bool = False):
        """
        【已升级】此函数现在能接收 force_fetch_from_tmdb 标志。
        它负责准备所有信息，并传递给核心处理函数。
        """
        if self.is_stop_requested(): 
            return False

        item_details = emby_handler.get_emby_item_details(emby_item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
        if not item_details:
            # 注意：这里不能直接调用 self.save_to_failed_log，因为它需要一个 cursor
            # 更好的做法是在调用者那里处理这个失败
            logger.error(f"process_single_item: 无法获取 Emby 项目 {emby_item_id} 的详情。")
            return False
        
        # 1. 决定本次运行是否应该处理分集
        if process_episodes is None:
            # 如果调用者没有明确指定，则遵循全局配置
            should_process_episodes_this_run = self.config.get(constants.CONFIG_OPTION_PROCESS_EPISODES, False)
        else:
            # 如果调用者明确指定了，就听从本次调用的指令
            should_process_episodes_this_run = process_episodes

        # 2. 将所有信息和决策，作为参数传递给核心处理函数
        return self._process_item_core_logic(
            item_details, 
            force_reprocess_this_item,
            should_process_episodes_this_run,
            force_fetch_from_tmdb=force_fetch_from_tmdb  # <--- 把新命令传递下去
        )

    def process_full_library(self, update_status_callback: Optional[callable] = None, process_episodes: bool = True, force_reprocess_all: bool = False, force_fetch_from_tmdb: bool = False):
        """
        【V3 - 最终完整版】
        这是所有全量扫描的唯一入口，它自己处理所有与“强制”相关的逻辑。
        """
        self.clear_stop_signal()
        
        logger.info(f"进入核心执行层: process_full_library, 接收到的 force_reprocess_all = {force_reprocess_all}, force_fetch_from_tmdb = {force_fetch_from_tmdb}")

        if force_reprocess_all:
            logger.info("检测到“强制重处理”选项，正在清空已处理日志...")
            try:
                self.clear_processed_log()
            except Exception as e:
                logger.error(f"在 process_full_library 中清空日志失败: {e}", exc_info=True)
                if update_status_callback: update_status_callback(-1, "清空日志失败")
                return

        # --- ★★★ 补全了这部分代码 ★★★ ---
        libs_to_process_ids = self.config.get("libraries_to_process", [])
        if not libs_to_process_ids:
            logger.warning("未在配置中指定要处理的媒体库。")
            return

        logger.info("正在尝试从Emby获取媒体项目...")
        all_emby_libraries = emby_handler.get_emby_libraries(self.emby_url, self.emby_api_key, self.emby_user_id) or []
        library_name_map = {lib.get('Id'): lib.get('Name', '未知库名') for lib in all_emby_libraries}
        
        movies = emby_handler.get_emby_library_items(self.emby_url, self.emby_api_key, "Movie", self.emby_user_id, libs_to_process_ids, library_name_map=library_name_map) or []
        series = emby_handler.get_emby_library_items(self.emby_url, self.emby_api_key, "Series", self.emby_user_id, libs_to_process_ids, library_name_map=library_name_map) or []
        
        if movies:
            source_movie_lib_names = sorted(list({library_name_map.get(item.get('_SourceLibraryId')) for item in movies if item.get('_SourceLibraryId')}))
            logger.info(f"从媒体库【{', '.join(source_movie_lib_names)}】获取到 {len(movies)} 个电影项目。")

        if series:
            source_series_lib_names = sorted(list({library_name_map.get(item.get('_SourceLibraryId')) for item in series if item.get('_SourceLibraryId')}))
            logger.info(f"从媒体库【{', '.join(source_series_lib_names)}】获取到 {len(series)} 个电视剧项目。")

        all_items = movies + series
        total = len(all_items)
        # --- ★★★ 补全结束 ★★★ ---
        
        if total == 0:
            logger.info("在所有选定的库中未找到任何可处理的项目。")
            if update_status_callback: update_status_callback(100, "未找到可处理的项目。")
            return

        for i, item in enumerate(all_items):
            if self.is_stop_requested(): break
            
            item_id = item.get('Id')
            item_name = item.get('Name', f"ID:{item_id}")

            if not force_reprocess_all and item_id in self.processed_items_cache:
                logger.info(f"正在跳过已处理的项目: {item_name}")
                if update_status_callback:
                    update_status_callback(int(((i + 1) / total) * 100), f"跳过: {item_name}")
                continue

            if update_status_callback:
                update_status_callback(int(((i + 1) / total) * 100), f"处理中 ({i+1}/{total}): {item_name}")
            
            self.process_single_item(
                item_id, 
                process_episodes=process_episodes,
                force_reprocess_this_item=force_reprocess_all,
                force_fetch_from_tmdb=force_fetch_from_tmdb
            )
            
            time.sleep(float(self.config.get("delay_between_items_sec", 0.5)))
        
        if not self.is_stop_requested() and update_status_callback:
            update_status_callback(100, "全量处理完成")
    # --- 一键翻译 ---
    def translate_cast_list_for_editing(self, 
                                    cast_list: List[Dict[str, Any]], 
                                    title: Optional[str] = None, 
                                    year: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        【V11 - 上下文感知版】为手动编辑页面提供的一键翻译功能。
        """
        if not cast_list:
            return []
            
        context_log = f" (上下文: {title} {year})" if title else ""
        logger.info(f"手动编辑-翻译：开始批量处理 {len(cast_list)} 位演员的姓名和角色{context_log}。")
        
        translated_cast = [dict(actor) for actor in cast_list]
        
        # --- 批量翻译逻辑 ---
        ai_translation_succeeded = False
        
        if self.ai_translator and self.config.get("ai_translation_enabled", False):
            texts_to_translate = set()
            for actor in translated_cast:
                for field_key in ['name', 'role']:
                    text = actor.get(field_key, '').strip()
                    if text and not utils.contains_chinese(text):
                        texts_to_translate.add(text)
            
            if texts_to_translate:
                logger.info(f"手动编辑-翻译：收集到 {len(texts_to_translate)} 个词条需要AI翻译。")
                try:
                    # 【★★★ 升级点 3：将上下文传递给AI顾问 ★★★】
                    translation_map = self.ai_translator.batch_translate(
                        texts=list(texts_to_translate),
                        title=title,
                        year=year
                    )
                    if translation_map:
                        # ... (后续的回填逻辑保持不变) ...
                        for i, actor in enumerate(translated_cast):
                            original_name = actor.get('name', '').strip()
                            if original_name in translation_map:
                                translated_cast[i]['name'] = translation_map[original_name]
                            original_role = actor.get('role', '').strip()
                            if original_role in translation_map:
                                translated_cast[i]['role'] = translation_map[original_role]
                        ai_translation_succeeded = True
                    else:
                        logger.warning("手动编辑-翻译：AI批量翻译未返回结果，将降级。")
                except Exception as e:
                    logger.error(f"手动编辑-翻译：调用AI批量翻译时出错: {e}，将降级。", exc_info=True)
        
        # 如果AI翻译未启用或失败，则降级到传统引擎
        if not ai_translation_succeeded:
            if self.config.get("ai_translation_enabled", False):
                logger.info("手动编辑-翻译：AI翻译失败，降级到传统引擎逐个翻译。")
            else:
                logger.info("手动编辑-翻译：AI未启用，使用传统引擎逐个翻译。")
                
            try:
                # 1. 使用 with 语句和中央函数，将所有数据库操作包裹起来
                with get_central_db_connection(self.db_path) as conn:
                    cursor = conn.cursor()

                    # 2. 您所有的翻译业务逻辑，原封不动地放在这里
                    for i, actor in enumerate(translated_cast):
                        actor_name_for_log = actor.get('name', '未知演员')
                        
                        # 翻译演员名
                        name_to_translate = actor.get('name', '').strip()
                        if name_to_translate and not utils.contains_chinese(name_to_translate):
                            translated_name = actor_utils.translate_actor_field(name_to_translate, "演员名(一键翻译)", name_to_translate, cursor)
                            if translated_name and translated_name != name_to_translate:
                                translated_cast[i]['name'] = translated_name
                                actor_name_for_log = translated_name

                        # 翻译角色名
                        role_to_translate = actor.get('role', '').strip()
                        if role_to_translate and not utils.contains_chinese(role_to_translate):
                            translated_role = actor_utils.translate_actor_field(role_to_translate, "角色名(一键翻译)", actor_name_for_log, cursor)
                            if translated_role and translated_role != role_to_translate:
                                translated_cast[i]['role'] = translated_role

                        if translated_cast[i].get('name') != actor.get('name') or translated_cast[i].get('role') != actor.get('role'):
                            translated_cast[i]['matchStatus'] = '已翻译'
                    
                    # 3. with 语句块在这里结束。
                    #    因为 translate_actor_field 内部可能会写入翻译缓存，
                    #    所以 with 语句在退出时会自动 commit 这些更改。
                    #    我们不再需要任何手动的 conn.commit(), conn.rollback(), conn.close()。

            except Exception as e:
                # 4. 这里的 except 块现在能捕获所有错误，包括连接数据库时的错误
                logger.error(f"一键翻译（降级模式）时发生错误: {e}", exc_info=True)
                # 注意：这里不需要返回 translated_cast，因为这个函数是直接修改列表内容的

        logger.info("手动编辑-翻译完成。")
        return translated_cast
    # ✨✨✨手动处理✨✨✨
    def process_item_with_manual_cast(self, item_id: str, manual_cast_list: List[Dict[str, Any]], item_name: str) -> bool:
        """
        【V4 - 后端缓存最终版】使用前端提交的轻量级修改，与内存中的完整数据合并。
        """
        logger.info(f"手动处理流程启动 (后端缓存模式)：ItemID: {item_id} ('{item_name}')")
        try:
            # ✨✨✨ 1. 使用 with 语句，在所有操作开始前获取数据库连接 ✨✨✨
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()

                # ✨✨✨ 2. 手动开启一个事务 ✨✨✨
                cursor.execute("BEGIN TRANSACTION;")
                logger.debug(f"手动处理 (ItemID: {item_id}) 的数据库事务已开启。")
            
                try:
                    # ★★★ 1. 从内存缓存中获取这个会话的完整原始演员列表 ★★★
                    original_full_cast = self.manual_edit_cache.get(item_id)
                    if not original_full_cast:
                        raise ValueError(f"在内存缓存中找不到 ItemID {item_id} 的原始演员数据。请重新进入编辑页面。")

                    # 2. 获取基础信息
                    item_details = emby_handler.get_emby_item_details(item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
                    if not item_details: raise ValueError(f"无法获取项目 {item_id} 的详情。")
                    tmdb_id = item_details.get("ProviderIds", {}).get("Tmdb")
                    item_type = item_details.get("Type")
                    if not tmdb_id: raise ValueError(f"项目 {item_id} 缺少 TMDb ID。")

                    # 3. 构建一个以 TMDb ID 为键的原始数据映射表，方便查找
                    reliable_cast_map = {str(actor['id']): actor for actor in original_full_cast if actor.get('id')}

                    # 4. 遍历前端传来的轻量级列表，安全地合并修改
                    final_cast_for_json = []
                    for actor_from_frontend in manual_cast_list:
                        frontend_tmdb_id = actor_from_frontend.get("tmdbId")
                        if not frontend_tmdb_id: continue

                        # 从我们的“真理之源”中找到对应的完整原始数据
                        original_actor_data = reliable_cast_map.get(str(frontend_tmdb_id))
                        if not original_actor_data:
                            logger.warning(f"在原始缓存中找不到 TMDb ID {frontend_tmdb_id}，跳过此演员。")
                            continue
                        
                        # 创建一个副本，并只更新 name 和 role
                        updated_actor_data = copy.deepcopy(original_actor_data)
                        updated_actor_data['name'] = actor_from_frontend.get('name')
                        updated_actor_data['character'] = actor_from_frontend.get('role')
                        
                        final_cast_for_json.append(updated_actor_data)
                    
                    # 为最终列表设置正确的顺序
                    for idx, actor in enumerate(final_cast_for_json):
                        actor['order'] = idx

                    # ★★★ 5. 后续的文件写入和刷新流程，现在使用的是100%完整的演员数据 ★★★
                    # ... (这部分逻辑与你之前的版本完全相同，我将它补全) ...
                    
                    cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
                    base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
                    base_json_filename = "all.json" if item_type == "Movie" else "series.json"
                    base_json_data_original = _read_local_json(os.path.join(base_cache_dir, base_json_filename)) or {}
                    base_json_data_for_override = copy.deepcopy(base_json_data_original)

                    if item_type == "Movie":
                        base_json_data_for_override.setdefault("casts", {})["cast"] = final_cast_for_json
                    else:
                        base_json_data_for_override.setdefault("credits", {})["cast"] = final_cast_for_json
                    
                    base_override_dir = os.path.join(self.local_data_path, "override", cache_folder_name, tmdb_id)
                    image_override_dir = os.path.join(base_override_dir, "images")
                    os.makedirs(image_override_dir, exist_ok=True)
                    override_json_path = os.path.join(base_override_dir, base_json_filename)
                    
                    temp_json_path = f"{override_json_path}.{random.randint(1000, 9999)}.tmp"
                    with open(temp_json_path, 'w', encoding='utf-8') as f:
                        json.dump(base_json_data_for_override, f, ensure_ascii=False, indent=4)
                    os.replace(temp_json_path, override_json_path)
                    logger.info(f"手动处理：成功生成覆盖元数据文件: {override_json_path}")

                    #---深度处理剧集
                    process_episodes_config = self.config.get(constants.CONFIG_OPTION_PROCESS_EPISODES, False)
                    if item_type == "Series" and process_episodes_config:
                        logger.info(f"手动处理：开始为所有分集注入手动编辑后的演员表...")
                        # base_cache_dir 变量在函数前面已经定义好了，可以直接使用
                        for filename in os.listdir(base_cache_dir):
                            if filename.startswith("season-") and filename.endswith(".json"):
                                child_json_original = _read_local_json(os.path.join(base_cache_dir, filename))
                                if child_json_original:
                                    child_json_for_override = copy.deepcopy(child_json_original)
                                    child_json_for_override.setdefault("credits", {})["cast"] = final_cast_for_json
                                    override_child_path = os.path.join(base_override_dir, filename)
                                    try:
                                        with open(override_child_path, 'w', encoding='utf-8') as f:
                                            json.dump(child_json_for_override, f, ensure_ascii=False, indent=4)
                                    except Exception as e:
                                        logger.error(f"手动处理：写入子项目JSON失败: {override_child_path}, {e}")

                    #---同步图片
                    if self.sync_images_enabled:
                        if self.is_stop_requested(): raise InterruptedError("任务中止")
                        logger.info(f"手动处理：开始下载图片...")
                        # image_override_dir 变量在函数前面已经定义好了，可以直接使用
                        image_map = {"Primary": "poster.jpg", "Backdrop": "fanart.jpg", "Logo": "clearlogo.png"}
                        if item_type == "Movie": image_map["Thumb"] = "landscape.jpg"
                        for image_type, filename in image_map.items():
                            emby_handler.download_emby_image(item_id, image_type, os.path.join(image_override_dir, filename), self.emby_url, self.emby_api_key)
                        
                        if item_type == "Series" and process_episodes_config:
                            # item_name 变量是从函数参数中传进来的，可以直接使用
                            children = emby_handler.get_series_children(item_id, self.emby_url, self.emby_api_key, self.emby_user_id, series_name_for_log=item_name) or []
                            for child in children:
                                if self.is_stop_requested(): break
                                child_type, child_id = child.get("Type"), child.get("Id")
                                if child_type == "Season":
                                    season_number = child.get("IndexNumber")
                                    if season_number is not None:
                                        emby_handler.download_emby_image(child_id, "Primary", os.path.join(image_override_dir, f"season-{season_number}.jpg"), self.emby_url, self.emby_api_key)
                                elif child_type == "Episode":
                                    season_number, episode_number = child.get("ParentIndexNumber"), child.get("IndexNumber")
                                    if season_number is not None and episode_number is not None:
                                        emby_handler.download_emby_image(child_id, "Primary", os.path.join(image_override_dir, f"season-{season_number}-episode-{episode_number}.jpg"), self.emby_url, self.emby_api_key)

                    logger.info(f"手动处理：准备刷新 Emby 项目 {item_name}...")
                    refresh_success = emby_handler.refresh_emby_item_metadata(
                        item_emby_id=item_id,
                        emby_server_url=self.emby_url,
                        emby_api_key=self.emby_api_key,
                        replace_all_metadata_param=True,
                        item_name_for_log=item_name
                    )
                    if not refresh_success:
                        # 即使刷新失败，文件也已经生成了，所以我们不让整个任务失败
                        # 但可以记录一个警告
                        logger.warning(f"手动处理：文件已生成，但触发 Emby 刷新失败。你可能需要稍后在 Emby 中手动刷新。")

                    # 更新处理日志
                    self.log_db_manager.save_to_processed_log(cursor, item_id, item_name, score=10.0)
                    self.log_db_manager.remove_from_failed_log(cursor, item_id)
                    
                    logger.info(f"✅ 手动处理 '{item_name}' 流程完成。")
                    return True
                
                except Exception as inner_e:
                    # 如果在事务中发生任何错误，回滚
                    logger.error(f"手动处理事务中发生错误 for {item_name}: {inner_e}", exc_info=True)
                    conn.rollback()
                    # 重新抛出，让外层捕获
                    raise

        except Exception as outer_e:
            logger.error(f"手动处理 '{item_name}' 时发生顶层错误: {outer_e}", exc_info=True)
            # 注意：这里的 save_to_failed_log 不能工作，因为它需要一个 cursor
            # 但这是一个更好的设计，因为如果数据库连接本身都失败了，我们也不应该尝试写入日志
            # 我们可以只在日志中记录这个错误
            # self.save_to_failed_log(item_id, item_name, f"手动处理异常: {str(e)}") # 这行需要移除或修改
            return False
        finally:
            # ★★★ 清理本次编辑会话的缓存 ★★★
            if item_id in self.manual_edit_cache:
                del self.manual_edit_cache[item_id]
                logger.debug(f"已清理 ItemID {item_id} 的内存缓存。")
    # --- 从本地 cache 文件获取演员列表用于编辑 ---
    def get_cast_for_editing(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        【V5 - 最终版】为手动编辑准备数据。
        1. 根据类型，从本地 cache 加载最完整的演员列表（电影直接加载，电视剧智能聚合）。
        2. 将完整列表缓存在内存中，供后续保存时使用。
        3. 只向前端发送轻量级数据 (ID, name, role, profile_path)。
        """
        logger.info(f"为编辑页面准备数据 (后端缓存模式)：ItemID {item_id}")
        
        try:
            # 1. 获取基础信息
            emby_details = emby_handler.get_emby_item_details(item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
            if not emby_details: raise ValueError(f"在Emby中未找到项目 {item_id}")

            tmdb_id = emby_details.get("ProviderIds", {}).get("Tmdb")
            item_type = emby_details.get("Type")
            if not tmdb_id: raise ValueError(f"项目 {item_id} 缺少 TMDb ID")

            # 2. ★★★ 根据类型决定如何从本地 cache 文件读取演员列表 ★★★
            full_cast_from_cache = []
            if item_type == "Movie":
                logger.debug(f"项目类型为电影，直接加载 all.json。")
                cache_folder_name = "tmdb-movies2"
                base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
                base_json_filename = "all.json"
                tmdb_data = _read_local_json(os.path.join(base_cache_dir, base_json_filename))
                if not tmdb_data: raise ValueError("未找到本地 TMDb 电影缓存文件")
                full_cast_from_cache = tmdb_data.get("credits", {}).get("cast", []) or tmdb_data.get("casts", {}).get("cast", [])
            
            elif item_type == "Series":
                logger.debug(f"项目类型为电视剧，调用聚合函数。")
                cache_folder_name = "tmdb-tv"
                base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
                # 【核心调用】调用我们已经完善的辅助函数来聚合所有演员
                full_cast_from_cache = self._get_full_tv_cast_from_cache(base_cache_dir)
                if not full_cast_from_cache: raise ValueError("未找到或无法聚合本地 TMDb 电视剧缓存")
            
            else:
                raise ValueError(f"不支持的 ItemType: {item_type} 用于演员编辑")

            # 3. 将完整的演员列表存入内存缓存
            self.manual_edit_cache[item_id] = full_cast_from_cache
            logger.debug(f"已为 ItemID {item_id} 缓存了 {len(full_cast_from_cache)} 条完整演员数据。")

            # 4. 构建并发送“轻量级”数据给前端
            cast_for_frontend = []
            for actor_data in full_cast_from_cache:
                actor_tmdb_id = actor_data.get('id')
                if not actor_tmdb_id: continue
                
                profile_path = actor_data.get('profile_path')
                image_url = f"https://image.tmdb.org/t/p/w185{profile_path}" if profile_path else None

                cast_for_frontend.append({
                    "tmdbId": actor_tmdb_id,
                    "name": actor_data.get('name'),
                    "role": actor_data.get('character'),
                    "imageUrl": image_url,
                })
            
            # 5. 获取失败日志信息和组合 response_data
            failed_log_info = {}
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT error_message, score FROM failed_log WHERE item_id = ?", (item_id,))
                row = cursor.fetchone()
                if row: failed_log_info = dict(row)

            response_data = {
                "item_id": item_id,
                "item_name": emby_details.get("Name"),
                "item_type": item_type,
                "image_tag": emby_details.get('ImageTags', {}).get('Primary'),
                "original_score": failed_log_info.get("score"),
                "review_reason": failed_log_info.get("error_message"),
                "current_emby_cast": cast_for_frontend,
                "search_links": {
                    "google_search_wiki": utils.generate_search_url('wikipedia', emby_details.get("Name"), emby_details.get("ProductionYear"))
                }
            }
            return response_data

        except Exception as e:
            logger.error(f"获取编辑数据失败 for ItemID {item_id}: {e}", exc_info=True)
            return None
    # ★★★ 全量图片同步的核心逻辑 ★★★
    def sync_all_images(self, update_status_callback: Optional[callable] = None):
        """
        【最终正确版】遍历所有已处理的媒体项，将它们在 Emby 中的当前图片下载到本地 override 目录。
        """
        logger.info("--- 开始执行全量海报同步任务 ---")
        
        try:
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT item_id, item_name FROM processed_log")
                items_to_process = cursor.fetchall()
        except Exception as e:
            logger.error(f"获取已处理项目列表时发生数据库错误: {e}", exc_info=True)
            if update_status_callback:
                update_status_callback(-1, "数据库错误")
            return

        total = len(items_to_process)
        if total == 0:
            logger.info("没有已处理的项目，无需同步图片。")
            if update_status_callback:
                update_status_callback(100, "没有项目")
            return

        logger.info(f"共找到 {total} 个已处理项目需要同步图片。")

        for i, db_row in enumerate(items_to_process):
            if self.is_stop_requested():
                logger.info("全量图片同步任务被中止。")
                break

            item_id = db_row['item_id']
            item_name_from_db = db_row['item_name']
            
            if not item_id:
                logger.warning(f"数据库中发现一条没有 item_id 的记录，跳过。Name: {item_name_from_db}")
                continue

            if update_status_callback:
                update_status_callback(int((i / total) * 100), f"同步图片 ({i+1}/{total}): {item_name_from_db}")

            try:
                item_details = emby_handler.get_emby_item_details(item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
                
                if not item_details:
                    logger.warning(f"跳过 {item_name_from_db} (ID: {item_id})，无法从 Emby 获取其详情。")
                    continue

                tmdb_id = item_details.get("ProviderIds", {}).get("Tmdb")
                item_type = item_details.get("Type")
                
                if not tmdb_id:
                    logger.warning(f"跳过 '{item_name_from_db}'，因为它缺少 TMDb ID。")
                    continue
                override_path = utils.get_override_path_for_item(item_type, tmdb_id, self.config)

                if not override_path:
                    logger.warning(f"跳过 '{item_name_from_db}'，无法为其生成有效的 override 路径 (可能是未知类型或配置问题)。")
                    continue

                image_override_dir = os.path.join(override_path, "images")
                os.makedirs(image_override_dir, exist_ok=True)

                image_map = {"Primary": "poster.jpg", "Backdrop": "fanart.jpg", "Logo": "clearlogo.png"}
                if item_type == "Movie":
                    image_map["Thumb"] = "landscape.jpg"
                
                logger.debug(f"项目 '{item_name_from_db}': 准备下载图片集到 '{image_override_dir}'")

                for image_type, filename in image_map.items():
                    emby_handler.download_emby_image(
                        item_id, 
                        image_type, 
                        os.path.join(image_override_dir, filename), 
                        self.emby_url, 
                        self.emby_api_key
                    )
                
                if item_type == "Series":
                    logger.info(f"开始为剧集 '{item_name_from_db}' 同步季海报...")
                    children = emby_handler.get_series_children(item_id, self.emby_url, self.emby_api_key, self.emby_user_id) or []
                    
                    for child in children:
                        # 只处理类型为 "Season" 的子项目，完全忽略 "Episode"
                        if child.get("Type") == "Season":
                            season_number = child.get("IndexNumber")
                            if season_number is not None:
                                logger.info(f"  正在同步第 {season_number} 季的海报...")
                                emby_handler.download_emby_image(
                                    child.get("Id"), 
                                    "Primary", # 季项目通常只有 Primary 图片
                                    os.path.join(image_override_dir, f"season-{season_number}.jpg"),
                                    self.emby_url, 
                                    self.emby_api_key
                                )
                
                logger.info(f"成功同步了 '{item_name_from_db}' 的图片。")

            except Exception as e:
                logger.error(f"同步项目 '{item_name_from_db}' (ID: {item_id}) 的图片时发生错误: {e}", exc_info=True)
            
            time.sleep(0.2)

        logger.info("--- 全量海报同步任务结束 ---")
    # --- 从本地 cache 获取完整演员表 ---
    def _get_full_tv_cast_from_cache(self, base_cache_dir: str) -> List[Dict[str, Any]]:
        """
        【V3 - 严格去重聚合版】从本地缓存中聚合电视剧的所有演员。
        修改为严格按ID和名字去重，确保没有同名演员。
        """
        import glob
        
        aggregated_cache_file = os.path.join(base_cache_dir, "_cast_aggregated.json")

        if os.path.exists(aggregated_cache_file):
            logger.debug(f"发现聚合演员缓存文件: {aggregated_cache_file}")
            cached_list = _read_local_json(aggregated_cache_file) or []
            
            # 【★★★ 终极加固：对读出的缓存进行强制去重 ★★★】
            # 防止旧的、被污染的缓存文件导致问题。
            final_list = []
            seen_ids = set()
            seen_names = set()
            is_dirty = False
            for actor in cached_list:
                actor_id = actor.get('id')
                actor_name = str(actor.get('name') or "").strip()
                if not actor_name: continue
                
                if (actor_id and actor_id in seen_ids) or (actor_name in seen_names):
                    is_dirty = True # 发现重复，标记缓存为脏
                    continue

                if actor_id: seen_ids.add(actor_id)
                seen_names.add(actor_name)
                final_list.append(actor)

            if is_dirty:
                logger.warning("检测到聚合缓存文件包含重复演员，已在内存中进行清理，并准备覆盖重写缓存。")
                self._write_aggregated_cast_cache(base_cache_dir, final_list) # 用干净的列表重写缓存
            
            return final_list

        logger.info(f"未找到聚合缓存，开始为 '{os.path.basename(base_cache_dir)}' 执行首次全量演员聚合...")
        
        # 【★★★ 核心修复：全新的、严格的去重聚合逻辑 ★★★】
        full_cast_list = []
        seen_ids = set()
        seen_names = set()

        def _add_cast_to_list(cast_list: List[Dict[str, Any]]):
            if not cast_list: return
            for actor_data in cast_list:
                if not isinstance(actor_data, dict): continue

                actor_id = actor_data.get('id')
                actor_name = str(actor_data.get('name') or "").strip()

                if not actor_name: continue # 忽略没有名字的演员

                # 1. ID去重
                if actor_id and actor_id in seen_ids: continue
                # 2. 名字去重
                if actor_name in seen_names: continue

                # 唯一演员，添加到列表并记录
                if actor_id: seen_ids.add(actor_id)
                seen_names.add(actor_name)
                full_cast_list.append(actor_data)
        
        all_json_files = glob.glob(os.path.join(base_cache_dir, '*.json'))
        all_json_files = [f for f in all_json_files if os.path.basename(f) != "_cast_aggregated.json"]
        
        if not all_json_files:
            logger.warning(f"在 {base_cache_dir} 中未找到任何源数据文件，无法执行聚合。")
            return []

        logger.debug(f"找到 {len(all_json_files)} 个源数据文件进行全量聚合。")

        for file_path in all_json_files:
            data = _read_local_json(file_path)
            if data:
                cast = data.get("credits", {}).get("cast", []) or data.get("casts", {}).get("cast", [])
                guest_stars = data.get("guest_stars", [])
                _add_cast_to_list(cast)
                _add_cast_to_list(guest_stars)

        logger.info(f"全量聚合完成，共获得 {len(full_cast_list)} 位独立演员。")

        self._write_aggregated_cast_cache(base_cache_dir, full_cast_list)

        return full_cast_list
    # --- 写入聚合缓存 ---
    def _write_aggregated_cast_cache(self, base_cache_dir: str, cast_list: List[Dict[str, Any]]):
        """专门用于写入聚合演员缓存的函数，会确保目录存在。"""
        if not cast_list:
            logger.debug("传入的演员列表为空，不创建聚合缓存文件。")
            return

        aggregated_cache_file = os.path.join(base_cache_dir, "_cast_aggregated.json")
        
        try:
            # ★★★ 关键修复：在写入前，确保父目录存在 ★★★
            os.makedirs(base_cache_dir, exist_ok=True)
            
            with open(aggregated_cache_file, 'w', encoding='utf-8') as f:
                json.dump(cast_list, f, ensure_ascii=False, indent=4)
            logger.info(f"已将 {len(cast_list)} 位演员的聚合结果写入缓存: {aggregated_cache_file}")
        except IOError as e:
            logger.error(f"写入聚合演员缓存失败: {e}")
    # --- 通过tmdb获取演员表 ---
    def _fetch_and_build_tmdb_base_json(self, tmdb_id: str, item_type: str, item_name: str) -> Optional[Dict[str, Any]]:
        """
        【已升级】直接从TMDb API获取媒体详情和演职员信息，并对电视剧进行在线聚合。
        """
        # ★★★ 优化日志 START ★★★
        # 1. 创建一个类型到中文的映射
        type_to_chinese = {
            "Movie": "电影",
            "Series": "电视剧"
        }
        # 2. 获取中文类型，如果找不到就用原始类型
        item_type_chinese = type_to_chinese.get(item_type, item_type)
        
        # 3. 打印全新的、友好的日志
        logger.info(f"[在线模式] 正在从TMDb API获取 {item_type_chinese} '{item_name}' (TMDb ID: {tmdb_id}) 的最新数据...")
        # ★★★ 优化日志 END ★★★
        
        if not self.tmdb_api_key:
            logger.error("TMDb API Key 未配置，无法执行在线获取。")
            return None
            
        try:
            tmdb_id_int = int(tmdb_id)
            if item_type == "Movie":
                # ★★★ 把 item_name 传递进去！ ★★★
                return tmdb_handler.get_movie_details_tmdb(
                    movie_id=tmdb_id_int, 
                    api_key=self.tmdb_api_key, 
                    append_to_response="credits,casts",
                    item_name=item_name # <--- 电影也有姓名了！
                )
            
            elif item_type == "Series":
                return tmdb_handler.get_full_tv_details_online(
                    tv_id=tmdb_id_int, 
                    api_key=self.tmdb_api_key, 
                    aggregation_level='first_episode',
                    item_name=item_name
                )
            
            else:
                logger.warning(f"不支持的类型 '{item_type}' 用于在线获取。")
                return None
                
        except Exception as e:
            logger.error(f"在线获取TMDb数据时发生错误: {e}", exc_info=True)
            return None
    # --- 一键删除本地TMDB缓存 ---
    def clear_tmdb_caches(self) -> Dict[str, Any]:
        """
        【新功能】一键清除所有TMDb相关的缓存和覆盖目录。
        这是一个高风险操作，会强制所有项目在下次处理时重新从在线获取。
        """
        if not self.local_data_path:
            msg = "未配置本地数据路径 (local_data_path)，无法执行清除操作。"
            logger.error(msg)
            return {"success": False, "message": msg, "details": {}}

        # 定义需要被清空的目标目录
        # 我们只清空这四个目录的 *内容*，而不删除目录本身
        target_subdirs = {
            "cache": ["tmdb-movies2", "tmdb-tv"],
            "override": ["tmdb-movies2", "tmdb-tv"]
        }

        report = {"success": True, "message": "TMDb缓存清除成功！", "details": {}}
        base_path = self.local_data_path
        
        logger.warning("!!! 开始执行高风险操作：清除TMDb缓存 !!!")

        for dir_type, subdirs in target_subdirs.items():
            report["details"][dir_type] = []
            for subdir_name in subdirs:
                full_path = os.path.join(base_path, dir_type, subdir_name)
                
                if os.path.isdir(full_path):
                    try:
                        # 遍历目录下的所有文件和子目录并删除
                        for item in os.listdir(full_path):
                            item_path = os.path.join(full_path, item)
                            if os.path.isdir(item_path):
                                shutil.rmtree(item_path) # 递归删除子目录
                            else:
                                os.remove(item_path) # 删除文件
                        
                        msg = f"成功清空目录: {full_path}"
                        logger.info(msg)
                        report["details"][dir_type].append(msg)

                    except Exception as e:
                        msg = f"清空目录 {full_path} 时发生错误: {e}"
                        logger.error(msg, exc_info=True)
                        report["details"][dir_type].append(msg)
                        report["success"] = False # 标记整个操作为失败
                        report["message"] = "清除过程中发生错误，部分缓存可能未被清除。"
                else:
                    msg = f"目录不存在，跳过: {full_path}"
                    logger.info(msg)
                    report["details"][dir_type].append(msg)
        
        if report["success"]:
            logger.info("✅ 所有指定的TMDb缓存目录已成功清空。")
        else:
            logger.error("❌ 清除TMDb缓存操作未完全成功，请检查日志。")
            
        return report
    def close(self):
        if self.douban_api: self.douban_api.close()
        logger.debug("MediaProcessor closed.")

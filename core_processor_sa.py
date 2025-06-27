# core_processor_sa.py

import os
import json
import sqlite3
from typing import Dict, List, Optional, Any, Tuple
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
from actor_utils import ActorDBManager, batch_translate_cast, evaluate_cast_processing_quality, select_best_role
from ai_translator import AITranslator
from utils import LogDBManager, get_override_path_for_item
from watchlist_processor import WatchlistProcessor
from douban import DoubanApi, clean_character_name_static
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

class MediaProcessorSA:
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

    def _get_db_connection(self) -> sqlite3.Connection:
        # ✨✨✨ 增加 timeout 参数，单位是秒 ✨✨✨
        # 5-10秒是一个比较合理的等待时间
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

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
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT item_id, item_name FROM processed_log")
            rows = cursor.fetchall()
            for row in rows:
                if row['item_id'] and row['item_name']:
                    log_dict[row['item_id']] = row['item_name']
            conn.close()
        except Exception as e:
            logger.error(f"从数据库读取已处理记录失败: {e}")
        return log_dict

    def clear_processed_log(self):
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM processed_log")
            conn.commit()
            conn.close()
            self.processed_items_cache.clear()
            logger.info("数据库和内存中的已处理记录已清除。")
        except Exception as e:
            logger.error(f"清除数据库已处理记录失败: {e}")

    def _translate_actor_field(self, text: Optional[str], field_name: str, actor_name_for_log: str, db_cursor: sqlite3.Cursor) -> Optional[str]:
        """
        【修复缓存逻辑版】翻译演员的特定字段，智能选择AI或传统翻译引擎，并正确处理缓存。
        """
        # 1. 前置检查：如果文本为空、是纯空格，或已包含中文，则直接返回原文
        if not text or not text.strip() or utils.contains_chinese(text):
            return text
        
        text_stripped = text.strip()

        # 2. 前置检查：跳过短的大写字母缩写
        if len(text_stripped) <= 2 and text_stripped.isupper():
            return text

        # 3. 核心修复：优先从数据库读取缓存，并处理所有情况
        cached_entry = DoubanApi._get_translation_from_db(text_stripped, cursor=db_cursor)
        if cached_entry:
            # 情况 A: 缓存中有成功的翻译结果
            if cached_entry.get("translated_text"):
                cached_translation = cached_entry.get("translated_text")
                logger.info(f"数据库翻译缓存命中 for '{text_stripped}' -> '{cached_translation}'")
                return cached_translation
            # 情况 B: 缓存中明确记录了这是一个失败的翻译
            else:
                logger.debug(f"数据库翻译缓存命中 (失败记录) for '{text_stripped}'，不再尝试在线翻译。")
                return text # 直接返回原文，避免重复请求

        # 4. 如果缓存中完全没有记录，才进行在线翻译
        logger.debug(f"'{text_stripped}' 在翻译缓存中未找到，将进行在线翻译...")
        final_translation = None
        final_engine = "unknown"

        # 根据配置选择翻译方式
        ai_translation_attempted = False

        # 步骤 1: 如果AI翻译启用，优先尝试AI
        if self.ai_translator and self.config.get("ai_translation_enabled", False):
            ai_translation_attempted = True
            logger.debug(f"AI翻译已启用，优先尝试使用 '{self.ai_translator.provider}' 进行翻译...")
            try:
                # ai_translator.translate 应该在失败时返回 None 或抛出异常
                ai_result = self.batch_translate_cast(text_stripped)
                if ai_result: # 确保AI返回了有效结果
                    final_translation = ai_result
                    final_engine = self.ai_translator.provider
            except Exception as e_ai:
                # 如果AI翻译器内部抛出异常，在这里捕获
                logger.error(f"AI翻译器在翻译 '{text_stripped}' 时发生异常: {e_ai}")
                # 不做任何事，让流程继续往下走，尝试传统引擎

        # 步骤 2: 如果AI翻译未启用，或AI翻译失败/未返回结果，则使用传统引擎
        if not final_translation:
            if ai_translation_attempted:
                logger.warning(f"AI翻译未能获取有效结果，将降级使用传统翻译引擎...")
            
            translation_result = utils.translate_text_with_translators(
                text_stripped,
                engine_order=self.translator_engines
            )
            if translation_result and translation_result.get("text"):
                final_translation = translation_result["text"]
                final_engine = translation_result["engine"]

        # 5. 处理在线翻译的结果，并更新缓存
        if final_translation and final_translation.strip() and final_translation.strip().lower() != text_stripped.lower():
            # 翻译成功，存入缓存并返回结果
            logger.debug(f"在线翻译成功: '{text_stripped}' -> '{final_translation}' (使用引擎: {final_engine})")
            DoubanApi._save_translation_to_db(text_stripped, final_translation, final_engine, cursor=db_cursor)
            return final_translation
        else:
            # 翻译失败或返回原文，将失败状态存入缓存，并返回原文
            logger.warning(f"在线翻译未能翻译 '{text_stripped}' 或返回了原文 (使用引擎: {final_engine})。")
            DoubanApi._save_translation_to_db(text_stripped, None, f"failed_or_same_via_{final_engine}", cursor=db_cursor)
            return text

    def _fetch_douban_cast(self, media_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        # 1. 检查 Douban API 是否可用
        if not (DOUBAN_API_AVAILABLE and self.douban_api):
            logger.warning("在线豆瓣功能不可用 (DoubanApi 未导入或初始化失败)，无法获取豆瓣演员。")
            return []

        # 2. 直接执行在线请求逻辑
        logger.debug("神医专用版：开始在线请求豆瓣 API 获取演员...")
        douban_data = self.douban_api.get_acting(
            name=media_info.get("Name"),
            imdbid=media_info.get("ProviderIds", {}).get("Imdb"),
            mtype="movie" if media_info.get("Type") == "Movie" else "tv",
            year=str(media_info.get("ProductionYear", "")),
            douban_id_override=media_info.get("ProviderIds", {}).get("Douban")
        )
        
        if douban_data and not douban_data.get("error"):
            return douban_data.get("cast", [])
        
        # 如果请求失败或返回错误，打印警告并返回空列表
        if douban_data and douban_data.get("error"):
            logger.warning(f"请求豆瓣 API 失败: {douban_data.get('message')}")
            
        return []

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
                # _format_douban_cast 需要的是一个列表，所以直接返回列表即可
                return douban_data.get('actors', [])
            else:
                logger.warning(f"本地豆瓣缓存文件 '{local_json_path}' 无效或不含 'actors' 键，将回退到在线API。")
        
        # 3. 如果本地未找到，回退到在线API
        logger.info("未找到本地豆瓣缓存，将通过在线API获取演员信息。")
        return self._fetch_douban_cast(media_info)

    def _format_douban_cast(self, douban_api_actors_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        formatted, seen_ids, seen_names = [], set(), set()
        for item in douban_api_actors_raw:
            # 兼容两种可能的来源：API返回的 'name' 和 本地缓存的 'name'/'latin_name'
            name_zh = str(item.get("name", "")).strip()
            if not name_zh: continue
            
            douban_id = str(item.get("id", "")).strip() or None
            
            # 兼容本地缓存的 'latin_name'
            original_name = str(item.get("original_name", "") or item.get("latin_name", "")).strip()

            if douban_id:
                if douban_id in seen_ids: continue
                seen_ids.add(douban_id)
            else:
                name_sig = f"{name_zh.lower()}|{original_name.lower()}"
                if name_sig in seen_names: continue
                seen_names.add(name_sig)
            
            formatted.append({
                "name": name_zh, "original_name": original_name,
                "character": str(item.get("character", "")).strip(), "douban_id": douban_id,
            })
        return formatted
    
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
            
    # --- 核心处理流程 ---
    def _process_cast_list_from_local(self, local_cast_list: List[Dict[str, Any]], emby_item_info: Dict[str, Any], cursor: sqlite3.Cursor, tmdb_api_key: Optional[str], stop_event: Optional[threading.Event]) -> List[Dict[str, Any]]:
        """
        【V10 - 批量翻译优化版】使用本地缓存或在线API获取豆瓣演员，再进行匹配和处理。
        """
        # ★★★ V-Final 优化：在源头过滤无头像演员 ★★★
        initial_count = len(local_cast_list)
        if self.config.get("filter_actors_without_avatar", True):
            logger.info(f"【头像过滤】开始检查 {initial_count} 位原始演员的头像信息...")
            
            cast_to_process_after_filter = [
                actor for actor in local_cast_list if actor.get("profile_path")
            ]
            
            filtered_count = initial_count - len(cast_to_process_after_filter)
            if filtered_count > 0:
                logger.info(f"【头像过滤】已过滤掉 {filtered_count} 位无头像的演员。")
            
            local_cast_list = cast_to_process_after_filter
        else:
            logger.info("【源头过滤】未启用无头像演员过滤功能。")
        # ★★★ 源头过滤结束 ★★★
        
        douban_candidates_raw = self._get_douban_cast_with_local_cache(emby_item_info)
        douban_candidates = self._format_douban_cast(douban_candidates_raw)
        
        final_cast_map = {actor['id']: actor for actor in local_cast_list if actor.get('id')}
        for actor in local_cast_list:
            if not actor.get('id'):
                name_key = f"name_{actor.get('name', '').lower()}"
                if name_key not in final_cast_map:
                    final_cast_map[name_key] = actor

        unmatched_douban_candidates = []

        # --- 步骤 1: 用豆瓣演员名和原始演员表匹配 ---
        logger.debug("--- 匹配阶段 1: 按名字匹配 ---")
        matched_douban_indices = set()
        for i, d_actor in enumerate(douban_candidates):
            for l_actor in final_cast_map.values():
                if utils.are_names_match(d_actor.get("name"), d_actor.get("original_name"), l_actor.get("name"), l_actor.get("original_name")):
                    logger.info(f"  匹配成功 (名字): 豆瓣演员 '{d_actor.get('name')}' -> 本地演员 '{l_actor.get('name')}'")
                    l_actor["name"] = d_actor.get("name")
                    cleaned_douban_character = utils.clean_character_name_static(d_actor.get("character"))
                    l_actor["character"] = select_best_role(
                        l_actor.get("character"), 
                        cleaned_douban_character 
                    )
                    if d_actor.get("douban_id"): l_actor["douban_id"] = d_actor.get("douban_id")
                    matched_douban_indices.add(i)
                    break
        
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
            
            logger.debug(f"--- 匹配阶段 2: 用豆瓣ID查 person_identity_map ({len(unmatched_douban_candidates)} 位演员) ---")
            still_unmatched = []
            for d_actor in unmatched_douban_candidates:
                if self.is_stop_requested(): raise InterruptedError("任务中止")
                d_douban_id = d_actor.get("douban_id")
                match_found = False
                if d_douban_id:
                    entry = self._find_person_in_map_by_douban_id(d_douban_id, cursor)
                    if entry and entry["tmdb_person_id"]:
                        tmdb_id_from_map = entry["tmdb_person_id"]
                        if tmdb_id_from_map not in final_cast_map:
                            logger.info(f"  新增成功 (数据库映射): 豆瓣演员 '{d_actor.get('name')}' -> 新增 TMDbID: {tmdb_id_from_map}")
                            new_actor_entry = {
                                "id": tmdb_id_from_map, "name": d_actor.get("name"), "original_name": d_actor.get("original_name"),
                                "character": d_actor.get("character"), "adult": False, "gender": 0, "known_for_department": "Acting",
                                "popularity": 0.0, "profile_path": None, "cast_id": None, "credit_id": None, "order": -1,
                                "imdb_id": entry["imdb_id"], "douban_id": d_douban_id, "_is_newly_added": True
                            }
                            final_cast_map[tmdb_id_from_map] = new_actor_entry
                        match_found = True
                if not match_found:
                    still_unmatched.append(d_actor)
            unmatched_douban_candidates = still_unmatched

            logger.debug(f"--- 匹配阶段 3: 跳过实时的TMDb/IMDb API反查 ---")
            if unmatched_douban_candidates:
                discarded_names = [d.get('name') for d in unmatched_douban_candidates]
                logger.info(f"--- 最终丢弃 {len(unmatched_douban_candidates)} 位无本地数据库匹配的豆瓣演员: {', '.join(discarded_names[:5])}{'...' if len(discarded_names) > 5 else ''} ---")


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
                    translation_map_from_api = self.ai_translator.batch_translate(list(texts_to_translate))
                    
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

            # 使用你原来的、健壮的逐个翻译逻辑作为回退
            for actor in cast_to_process:
                if self.is_stop_requested():
                    raise InterruptedError("任务在翻译演员列表时被中止")
                
                # _translate_actor_field 本身就有缓存和多引擎逻辑，非常适合做降级
                actor['name'] = self._translate_actor_field(actor.get('name'), "演员名", actor.get('name'), cursor)
                
                cleaned_character = utils.clean_character_name_static(actor.get('character'))
                translated_character = self._translate_actor_field(cleaned_character, "角色名", actor.get('name'), cursor)
                actor['character'] = translated_character

        # 返回处理完的、已经截断和翻译的列表
        return cast_to_process
        
    # ✨✨✨格式化演员表✨✨✨
    def _format_and_complete_cast_list(self, cast_list: List[Dict[str, Any]], is_animation: bool) -> List[Dict[str, Any]]:
        perfect_cast = []
        logger.info("格式化演员列表：开始处理角色名和排序。")

        for idx, actor in enumerate(cast_list):
            # 1. 获取原始角色名
            raw_role = actor.get("character", "").strip()

            # 2. ★★★ 预处理：调用 utils.clean_character_name_static ★★★
            cleaned_role = utils.clean_character_name_static(raw_role)
            logger.debug(f"[角色名清洗] 原始: {repr(raw_role)} → 清洗后: {repr(cleaned_role)}")

            # 3. 如果包含中文，移除所有空格（包括全角空格）
            if utils.contains_chinese(cleaned_role):
                cleaned_role = cleaned_role.replace(" ", "").replace("　", "")
                logger.debug(f"[移除中文空格] → {repr(cleaned_role)}")

            # 4. 根据是否为动画，处理“配音”或默认“演员”
            if is_animation:
                if cleaned_role and not cleaned_role.endswith("(配音)"):
                    final_role = f"{cleaned_role} (配音)"
                elif not cleaned_role:
                    final_role = "配音"
                else:
                    final_role = cleaned_role
            else:
                final_role = cleaned_role if cleaned_role else "演员"

            # 5. 写入角色和排序
            actor["character"] = final_role
            actor["order"] = idx
            perfect_cast.append(actor)

            logger.debug(f"[最终角色名] {repr(final_role)}")

        return perfect_cast
    # ✨✨✨API中文化演员表✨✨✨
    def _process_api_track_person_names_only(self, item_details, cursor: sqlite3.Cursor):
        """
        【API轨道 - 批量翻译重构版】
        此函数负责将指定媒体项目中演员的英文名批量翻译成中文，并更新回Emby。
        """
        item_id = item_details.get("Id")
        item_name_for_log = item_details.get("Name", f"未知媒体(ID:{item_id})")
        logger.info(f"前置翻译开始为 '{item_name_for_log}' 进行演员名批量中文化...")

        # 1. 从 Emby 获取原始演员列表
        original_cast = item_details.get("People", [])
        if not original_cast:
            logger.info("前置翻译：该媒体在Emby中没有演员信息，跳过。")
            return

        # 检查 AI 翻译器和配置是否就绪
        if not self.ai_translator or not self.config.get("ai_translation_enabled", False):
            logger.warning("前置翻译：AI翻译器未配置或未启用，跳过演员名中文化。")
            return

        try:
            with self._get_db_connection() as conn:
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
                    logger.info(f"前置翻译：为 '{item_name_for_log}' 收集到 {len(texts_to_translate)} 个演员名需要通过AI翻译。")
                    try:
                        # 调用底层的批量翻译方法
                        translation_map = self.ai_translator.batch_translate(list(texts_to_translate))
                        
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
                                    logger.info(f"  【API轨道】准备更新: '{original_name}' -> '{translated_name}' (Emby Person ID: {person_id})")
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

    def _process_item_core_logic(self, item_details_from_emby: Dict[str, Any], force_reprocess_this_item: bool = False) -> bool:
        """
        【V-Final 事务优化版】
        在一个统一的数据库事务中，串行执行所有数据库相关的处理轨道。
        """
        item_id = item_details_from_emby.get("Id")
        item_name_for_log = item_details_from_emby.get("Name", f"未知项目(ID:{item_id})")
        tmdb_id = item_details_from_emby.get("ProviderIds", {}).get("Tmdb")
        item_type = item_details_from_emby.get("Type")

        logger.debug(f"--- 开始核心处理: '{item_name_for_log}' (TMDbID: {tmdb_id}) ---")

        if self.is_stop_requested():
            logger.info(f"任务在处理 '{item_name_for_log}' 前被中止。")
            return False

        try:
            # ✨✨✨ 1. 使用 with 语句管理唯一的数据库连接 ✨✨✨
            # 注意：这里我们不再使用 self._get_db_connection()，而是直接调用 manager 的方法
            with self.actor_db_manager.get_db_connection() as conn:
                cursor = conn.cursor()

                # ✨✨✨ 2. 在所有操作开始前，开启一个总事务 ✨✨✨
                cursor.execute("BEGIN TRANSACTION;")
                logger.debug(f"媒体 '{item_name_for_log}' 的总数据库事务已开启。")

                try:
                    # ★★★ 轨道一：API 轨道 (仅中文化演员名) ★★★
                    # 我们需要修改 _process_api_track_person_names_only，让它接收 cursor
                    self._process_api_track_person_names_only(item_details_from_emby, cursor)
                    
                    if self.is_stop_requested(): raise InterruptedError("任务被中止")

                    # ★★★ 轨道二：JSON 轨道 (神医模式核心) ★★★
                    logger.info(f"开始处理JSON元数据并生成到覆盖缓存目录 ---")
                    if not tmdb_id or not self.local_data_path:
                        error_msg = "缺少TMDbID" if not tmdb_id else "未配置本地数据路径"
                        logger.warning(f"【JSON轨道】跳过处理 '{item_name_for_log}'，原因: {error_msg}。")
                        self.save_to_failed_log(item_id, item_name_for_log, f"JSON轨道预处理失败: {error_msg}", item_type)
                        # 注意：这里跳过不应该回滚，因为API轨道可能已经成功，所以我们直接提交
                        conn.commit()
                        return False
                    
                    # --- 阶段1: 演员处理 (现在都在事务中) ---
                    # (路径和数据准备逻辑保持不变)
                    cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
                    base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
                    base_override_dir = os.path.join(self.local_data_path, "override", cache_folder_name, tmdb_id)
                    image_override_dir = os.path.join(base_override_dir, "images")
                    os.makedirs(image_override_dir, exist_ok=True)
                    base_json_filename = "all.json" if item_type == "Movie" else "series.json"
                    base_json_data_original = _read_local_json(os.path.join(base_cache_dir, base_json_filename))
                    if not base_json_data_original:
                        raise ValueError(f"无法读取基础JSON文件: {os.path.join(base_cache_dir, base_json_filename)}")

                    original_cast_from_local = base_json_data_original.get("credits", {}).get("cast") or base_json_data_original.get("casts", {}).get("cast", [])
                    initial_actor_count = len(original_cast_from_local)
                    
                    # 调用 _process_cast_list_from_local，它现在会在我们的事务中运行
                    intermediate_cast = self._process_cast_list_from_local(
                        original_cast_from_local, 
                        item_details_from_emby, 
                        cursor, # <--- 传递事务中的 cursor
                        self.tmdb_api_key,
                        self.get_stop_event() # 假设 get_stop_event 返回事件对象
                    )
                    
                    genres = item_details_from_emby.get("Genres", [])
                    is_animation = "Animation" in genres or "动画" in genres
                    
                    final_cast_perfect = self._format_and_complete_cast_list(intermediate_cast, is_animation)
            
                    # --- 阶段2: 文件写入 (不涉及数据库) ---
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

                    if item_type == "Series" and self.config.get(constants.CONFIG_OPTION_PROCESS_EPISODES, False):
                        logger.info(f"开始为 '{item_name_for_log}' 的所有分集注入演员表...")
                        for filename in os.listdir(base_cache_dir):
                            if filename.startswith("season-") and filename.endswith(".json"):
                                child_json_original = _read_local_json(os.path.join(base_cache_dir, filename))
                                if child_json_original:
                                    child_json_for_override = copy.deepcopy(child_json_original)
                                    child_json_for_override.setdefault("credits", {})["cast"] = final_cast_perfect
                                    override_child_path = os.path.join(base_override_dir, filename)
                                    try:
                                        with open(override_child_path, 'w', encoding='utf-8') as f:
                                            json.dump(child_json_for_override, f, ensure_ascii=False, indent=4)
                                    except Exception as e:
                                        logger.error(f"写入子项目JSON失败: {override_child_path}, {e}")

                    if self.sync_images_enabled:
                        if self.is_stop_requested(): raise InterruptedError("任务被中止")
                        logger.info(f"开始为 '{item_name_for_log}' 下载图片...")
                        image_map = {"Primary": "poster.jpg", "Backdrop": "fanart.jpg", "Logo": "clearlogo.png"}
                        if item_type == "Movie": image_map["Thumb"] = "landscape.jpg"
                        for image_type, filename in image_map.items():
                            emby_handler.download_emby_image(item_id, image_type, os.path.join(image_override_dir, filename), self.emby_url, self.emby_api_key)
                        
                        if item_type == "Series" and self.config.get(constants.CONFIG_OPTION_PROCESS_EPISODES, False):
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
                    logger.info(f"  - 原有演员: {initial_actor_count} 位")
                    newly_added_count = final_actor_count - initial_actor_count
                    if newly_added_count > 0:
                        logger.info(f"  - 新增演员: {newly_added_count} 位")
                    logger.info(f"  - 最终演员: {final_actor_count} 位")

                    if self.is_stop_requested(): raise InterruptedError("任务被中止")

                    # ★★★ 核心修改：在调用时，传入预期的最终数量 ★★★
                    processing_score = evaluate_cast_processing_quality(
                        final_cast=final_cast_perfect,
                        original_cast_count=initial_actor_count,
                        expected_final_count=len(final_cast_perfect), # 把截断后的数量告诉评估函数
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
                    
                    logger.info(f"✨✨✨处理完成 '{item_name_for_log}'，提交数据库总事务✨✨✨")
                    conn.commit()
                    return True

                except InterruptedError:
                    logger.info(f"处理 '{item_name_for_log}' 的过程中被用户中止。")
                    logger.warning("正在回滚数据库事务...")
                    conn.rollback()
                    return False
        except Exception as inner_e:
            logger.error(f"在事务处理中发生错误 for media '{item_name_for_log}': {inner_e}", exc_info=True)
            logger.warning("正在回滚数据库事务...")
            conn.rollback()
            self.log_db_manager.save_to_failed_log(
                cursor, item_id, item_name_for_log, f"核心处理异常: {str(inner_e)}", item_type
            )
            return False

        

    def process_single_item(self, emby_item_id: str, force_reprocess_this_item: bool = False, process_episodes: bool = True) -> bool:
        if self.is_stop_requested(): return False
        #if not force_reprocess_this_item and emby_item_id in self.processed_items_cache: return True

        item_details = emby_handler.get_emby_item_details(emby_item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
        if not item_details:
            self.save_to_failed_log(emby_item_id, f"未知项目(ID:{emby_item_id})", "无法获取Emby项目详情")
            return False
        
        self.config[constants.CONFIG_OPTION_PROCESS_EPISODES] = process_episodes
        
        return self._process_item_core_logic(item_details, force_reprocess_this_item)

    def process_full_library(self, update_status_callback: Optional[callable] = None, force_reprocess_all: bool = False, process_episodes: bool = True):
        self.clear_stop_signal()
        
        libs_to_process_ids = self.config.get("libraries_to_process", [])
        if not libs_to_process_ids:
            logger.warning("未在配置中指定要处理的媒体库。")
            return

        # --- 步骤 1: 获取库名对照表 ---
        logger.info("正在尝试从Emby获取媒体项目...")
        all_emby_libraries = emby_handler.get_emby_libraries(self.emby_url, self.emby_api_key, self.emby_user_id) or []
        library_name_map = {lib.get('Id'): lib.get('Name', '未知库名') for lib in all_emby_libraries}
        logger.debug(f"已生成媒体库名称对照表: {library_name_map}")

        # --- 步骤 2: 分别获取电影和电视剧 ---
        movies = emby_handler.get_emby_library_items(self.emby_url, self.emby_api_key, "Movie", self.emby_user_id, libs_to_process_ids, library_name_map=library_name_map) or []
        series = emby_handler.get_emby_library_items(self.emby_url, self.emby_api_key, "Series", self.emby_user_id, libs_to_process_ids, library_name_map=library_name_map) or []
        
        # --- 步骤 3: 汇总和打印漂亮的日志 ---
        if movies:
            source_movie_lib_ids = sorted(list({item.get('_SourceLibraryId') for item in movies if item.get('_SourceLibraryId')}))
            source_movie_lib_names = [library_name_map.get(id, str(id)) for id in source_movie_lib_ids]
            logger.info(f"从媒体库【{', '.join(source_movie_lib_names)}】获取到 {len(movies)} 个电影项目。")

        if series:
            source_series_lib_ids = sorted(list({item.get('_SourceLibraryId') for item in series if item.get('_SourceLibraryId')}))
            source_series_lib_names = [library_name_map.get(id, str(id)) for id in source_series_lib_ids]
            logger.info(f"从媒体库【{', '.join(source_series_lib_names)}】获取到 {len(series)} 个电视剧项目。")

        # --- 步骤 4: 合并并继续后续处理 ---
        all_items = movies + series
        
        total = len(all_items)
        if total == 0:
            logger.info("在所有选定的库中未找到任何可处理的项目。")
            if update_status_callback:
                update_status_callback(100, "未找到可处理的项目。")
            return

        for i, item in enumerate(all_items):
            if self.is_stop_requested(): break
            
            item_id = item.get('Id')
            item_name = item.get('Name', f"ID:{item_id}")

            # ★★★ 核心修改：这里的跳过逻辑现在对所有情况都生效 ★★★
            # 不再需要 if not force_reprocess_all ...
            if item_id in self.processed_items_cache:
                logger.info(f"正在跳过已处理的项目: {item_name}")
                if update_status_callback:
                    update_status_callback(int(((i + 1) / total) * 100), f"跳过: {item_name}")
                continue

            if update_status_callback:
                update_status_callback(int(((i + 1) / total) * 100), f"处理中 ({i+1}/{total}): {item_name}")
            
            # ★★★ 核心修改：调用时不再需要传递 force_reprocess_all ★★★
            self.process_single_item(item_id, process_episodes=process_episodes)
            
            time.sleep(float(self.config.get("delay_between_items_sec", 0.5)))
        
        if not self.is_stop_requested() and update_status_callback:
            update_status_callback(100, "全量处理完成")
    # --- 一键翻译 ---
    def translate_cast_list_for_editing(self, cast_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        【新 - 批量优化版】为手动编辑页面提供的一键翻译功能。
        """
        if not cast_list:
            return []
            
        logger.info(f"手动编辑-翻译：开始批量处理 {len(cast_list)} 位演员的姓名和角色。")
        translated_cast = [dict(actor) for actor in cast_list]
        
        # --- 批量翻译逻辑 ---
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
                logger.info(f"手动编辑-翻译：收集到 {len(texts_to_translate)} 个词条需要AI翻译。")
                try:
                    translation_map = self.ai_translator.batch_translate(list(texts_to_translate))
                    if translation_map:
                        logger.info(f"手动编辑-翻译：AI批量翻译成功，返回 {len(translation_map)} 个结果。")
                        
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
                
            conn = self._get_db_connection()
            try:
                cursor = conn.cursor()
                for i, actor in enumerate(translated_cast):
                    actor_name_for_log = actor.get('name', '未知演员')
                    
                    # 翻译演员名
                    name_to_translate = actor.get('name', '').strip()
                    if name_to_translate:
                        translated_name = self._translate_actor_field(name_to_translate, "演员名", actor_name_for_log, cursor)
                        if translated_name and translated_name != name_to_translate:
                            translated_cast[i]['name'] = translated_name
                            actor_name_for_log = translated_name

                    # 翻译角色名
                    role_to_translate = actor.get('role', '').strip()
                    if role_to_translate:
                        translated_role = self._translate_actor_field(role_to_translate, "角色名", actor_name_for_log, cursor)
                        if translated_role and translated_role != role_to_translate:
                            translated_cast[i]['role'] = translated_role
                
                conn.commit()
            except Exception as e:
                logger.error(f"手动编辑-翻译（降级模式）时发生错误: {e}", exc_info=True)
                if conn: conn.rollback()
            finally:
                if conn: conn.close()

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
            with self.actor_db_manager.get_db_connection() as conn:
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
                    process_episodes_config = self.config.get(constants.CONFIG_OPTION_PROCESS_EPISODES, True)
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
        【V4 - 后端缓存最终版】为手动编辑准备数据。
        1. 从本地 cache 加载完整的演员列表。
        2. 将完整列表缓存在内存中。
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

            # 2. 从本地 cache 文件读取最可靠的演员列表
            cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
            base_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
            base_json_filename = "all.json" if item_type == "Movie" else "series.json"
            tmdb_data = _read_local_json(os.path.join(base_cache_dir, base_json_filename))
            if not tmdb_data: raise ValueError("未找到本地 TMDb 缓存文件")
            
            full_cast_from_cache = tmdb_data.get("credits", {}).get("cast", []) or tmdb_data.get("casts", {}).get("cast", [])

            # ★★★ 3. 将完整的演员列表存入内存缓存 ★★★
            # 使用 item_id 作为键，确保每个编辑会话都是独立的
            self.manual_edit_cache[item_id] = full_cast_from_cache
            logger.debug(f"已为 ItemID {item_id} 缓存了 {len(full_cast_from_cache)} 条完整演员数据。")

            # 4. 构建并发送“轻量级”数据给前端
            cast_for_frontend = []
            for actor_data in full_cast_from_cache:
                actor_tmdb_id = actor_data.get('id')
                if not actor_tmdb_id: continue
                
                # 直接拼接 TMDb 头像链接，使用最小尺寸 w185
                profile_path = actor_data.get('profile_path')
                image_url = f"https://image.tmdb.org/t/p/w185{profile_path}" if profile_path else None

                cast_for_frontend.append({
                    "tmdbId": actor_tmdb_id,
                    "name": actor_data.get('name'),
                    "role": actor_data.get('character'),
                    "imageUrl": image_url, # 发送拼接好的完整 URL
                })
            
            # ... (从 failed_log 获取信息和组合 response_data 的逻辑保持不变) ...
            failed_log_info = {}
            with self._get_db_connection() as conn:
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
            with self._get_db_connection() as conn:
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

    
    def close(self):
        if self.douban_api: self.douban_api.close()
        logger.debug("MediaProcessor closed.")

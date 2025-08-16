# core_processor_sa.py

import os
import json
import sqlite3
import concurrent.futures
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
from cachetools import TTLCache
from db_handler import ActorDBManager
from db_handler import get_db_connection as get_central_db_connection
from ai_translator import AITranslator
from utils import LogDBManager, get_override_path_for_item, translate_country_list
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
def _save_metadata_to_cache(
    cursor: sqlite3.Cursor,
    tmdb_id: str,
    item_type: str,
    item_details_from_emby: Dict[str, Any], # ★ Emby 的详情
    final_processed_cast: List[Dict[str, Any]], # ★ 我们最终处理好的演员表
    tmdb_details_for_extra: Optional[Dict[str, Any]] # ★ 可选的、用于补充导演和国家的 TMDB 详情
):
    """
    【V-API-Native - API原生版】
    直接从处理好的数据（Emby详情、最终演员表、可选的TMDB详情）组装并缓存元数据。
    """
    try:
        logger.trace(f"【实时缓存】正在为 '{item_details_from_emby.get('Name')}' 组装元数据...")
        
        # --- 从我们已有的、最可靠的数据源中组装所有信息 ---
        
        # 1. 演员 (来自我们最终处理好的演员表)
        actors = [
            {"id": p.get("id"), "name": p.get("name"), "original_name": p.get("original_name")}
            for p in final_processed_cast
        ]

        # 2. 导演和国家 (优先从补充的 TMDB 详情中获取)
        directors, countries = [], []
        if tmdb_details_for_extra:
            if item_type == 'Movie':
                credits_data = tmdb_details_for_extra.get("credits", {}) or tmdb_details_for_extra.get("casts", {})
                if credits_data:
                    directors = [{'id': p.get('id'), 'name': p.get('name')} for p in credits_data.get('crew', []) if p.get('job') == 'Director']
                country_names = [c['name'] for c in tmdb_details_for_extra.get('production_countries', [])]
                countries = translate_country_list(country_names)
            elif item_type == 'Series':
                credits_data = tmdb_details_for_extra.get("credits", {})
                if credits_data:
                    directors = [{'id': p.get('id'), 'name': p.get('name')} for p in credits_data.get('crew', []) if p.get('job') == 'Director']
                if not directors:
                    directors = [{'id': c.get('id'), 'name': c.get('name')} for c in tmdb_details_for_extra.get('created_by', [])]
                country_codes = tmdb_details_for_extra.get('origin_country', [])
                countries = translate_country_list(country_codes)
        
        # 3. 其他信息 (主要从 Emby 详情中获取，因为这是最实时的)
        studios = [s['Name'] for s in item_details_from_emby.get('Studios', [])]
        genres = item_details_from_emby.get('Genres', [])
        release_date_str = (item_details_from_emby.get('PremiereDate') or '0000-01-01T00:00:00.000Z').split('T')[0]
        
        # --- 准备要存入数据库的数据 ---
        metadata = {
            "tmdb_id": tmdb_id,
            "item_type": item_type,
            "title": item_details_from_emby.get('Name'),
            "original_title": item_details_from_emby.get('OriginalTitle'),
            "release_year": item_details_from_emby.get('ProductionYear'),
            "rating": item_details_from_emby.get('CommunityRating'),
            "genres_json": json.dumps(genres, ensure_ascii=False),
            "actors_json": json.dumps(actors, ensure_ascii=False),
            "directors_json": json.dumps(directors, ensure_ascii=False),
            "studios_json": json.dumps(studios, ensure_ascii=False),
            "countries_json": json.dumps(countries, ensure_ascii=False),
            "date_added": (item_details_from_emby.get("DateCreated") or '').split('T')[0] or None,
            "release_date": release_date_str,
        }
        
        # --- 数据库写入 ---
        columns = ', '.join(metadata.keys())
        placeholders = ', '.join('?' for _ in metadata)
        sql = f"INSERT OR REPLACE INTO media_metadata ({columns}) VALUES ({placeholders})"
        cursor.execute(sql, tuple(metadata.values()))
        logger.debug(f"  -> 成功将《{metadata.get('title')}》的元数据缓存到数据库。")

    except Exception as e:
        logger.error(f"保存元数据到缓存表时失败: {e}", exc_info=True)
def _aggregate_series_cast_from_tmdb_data(series_data: Dict[str, Any], all_episodes_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    【新】从内存中的TMDB数据聚合一个剧集的所有演员。
    """
    logger.debug(f"【演员聚合】开始为 '{series_data.get('name')}' 从内存中的TMDB数据聚合演员...")
    aggregated_cast_map = {}

    # 1. 优先处理主剧集的演员列表
    main_cast = series_data.get("credits", {}).get("cast", [])
    for actor in main_cast:
        actor_id = actor.get("id")
        if actor_id:
            aggregated_cast_map[actor_id] = actor
    logger.debug(f"  -> 从主剧集数据中加载了 {len(aggregated_cast_map)} 位主演员。")

    # 2. 聚合所有分集的演员和客串演员
    for episode_data in all_episodes_data:
        credits_data = episode_data.get("credits", {})
        actors_to_process = credits_data.get("cast", []) + credits_data.get("guest_stars", [])
        
        for actor in actors_to_process:
            actor_id = actor.get("id")
            if actor_id and actor_id not in aggregated_cast_map:
                if 'order' not in actor:
                    actor['order'] = 999  # 为客串演员设置高order值
                aggregated_cast_map[actor_id] = actor

    full_aggregated_cast = list(aggregated_cast_map.values())
    full_aggregated_cast.sort(key=lambda x: x.get('order', 999))
    
    logger.info(f"  -> 共为 '{series_data.get('name')}' 聚合了 {len(full_aggregated_cast)} 位独立演员。")
    return full_aggregated_cast
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

                # 1. 从配置中获取冷却时间 
                douban_cooldown = self.config.get(constants.CONFIG_OPTION_DOUBAN_DEFAULT_COOLDOWN, 2.0)
                
                # 2. 从配置中获取 Cookie，使用我们刚刚在 constants.py 中定义的常量
                douban_cookie = self.config.get(constants.CONFIG_OPTION_DOUBAN_COOKIE, "")
                
                # 3. 添加一个日志，方便调试
                if not douban_cookie:
                    logger.debug(f"配置文件中未找到或未设置 '{constants.CONFIG_OPTION_DOUBAN_COOKIE}'。如果豆瓣API返回'need_login'错误，请配置豆瓣cookie。")
                else:
                    logger.debug("已从配置中加载豆瓣 Cookie。")

                # 4. 将所有参数传递给 DoubanApi 的构造函数
                self.douban_api = DoubanApi(
                    cooldown_seconds=douban_cooldown,
                    user_cookie=douban_cookie  # <--- 将 cookie 传进去
                )
                logger.trace("DoubanApi 实例已在 MediaProcessorAPI 中创建。")
                
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
        self.auto_lock_cast_enabled = self.config.get(constants.CONFIG_OPTION_AUTO_LOCK_CAST, True)
        
        self.ai_enabled = self.config.get("ai_translation_enabled", False)
        self.ai_translator = AITranslator(self.config) if self.ai_enabled else None
        
        self._stop_event = threading.Event()
        self.processed_items_cache = self._load_processed_log_from_db()
        self.manual_edit_cache = TTLCache(maxsize=10, ttl=600)
        logger.trace("核心处理器初始化完成。")
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
    # ★★★★★★★★★★★★★★★ 新增的、优雅的内部辅助方法 ★★★★★★★★★★★★★★★
    def _enrich_cast_from_db_and_api(self, cast_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        在内部处理 sqlite3.Row，但对外返回标准的 dict 列表，确保下游兼容性。
        """
        if not cast_list:
            return []
        
        logger.info(f"  -> 处理 {len(cast_list)} 位演员...")

        original_actor_map = {str(actor.get("Id")): actor for actor in cast_list if actor.get("Id")}
        
        # --- 阶段一：从本地数据库获取数据 ---
        enriched_actors_map = {}
        ids_found_in_db = set()
        
        try:
            # ★★★★★★★★★★★★★★★ 关键修改：在这里获取连接并设置 row_factory ★★★★★★★★★★★★★★★
            with get_central_db_connection(self.db_path) as conn:
                # conn.row_factory = sqlite3.Row # 假设 get_central_db_connection 已经设置了
                cursor = conn.cursor()
                person_ids = list(original_actor_map.keys())
                if person_ids:
                    placeholders = ','.join('?' for _ in person_ids)
                    query = f"SELECT * FROM person_identity_map WHERE emby_person_id IN ({placeholders})"
                    cursor.execute(query, person_ids)
                    db_results = cursor.fetchall()

                    for row in db_results:
                        # ★★★★★★★★★★★★★★★ 关键修改：立即将 sqlite3.Row 转换为 dict ★★★★★★★★★★★★★★★
                        db_data = dict(row)
                        
                        actor_id = str(db_data["emby_person_id"])
                        ids_found_in_db.add(actor_id)
                        
                        provider_ids = {}
                        # 现在可以安全地使用 .get() 方法了
                        if db_data.get("tmdb_person_id"): provider_ids["Tmdb"] = str(db_data.get("tmdb_person_id"))
                        if db_data.get("imdb_id"): provider_ids["Imdb"] = db_data.get("imdb_id")
                        if db_data.get("douban_celebrity_id"): provider_ids["Douban"] = str(db_data.get("douban_celebrity_id"))
                        
                        enriched_actor = original_actor_map[actor_id].copy()
                        enriched_actor["ProviderIds"] = provider_ids
                        enriched_actors_map[actor_id] = enriched_actor
        except Exception as e:
            logger.error(f"  -> 数据库查询阶段失败: {e}", exc_info=True)

        logger.info(f"  -> 阶段一 (数据库) 完成：找到了 {len(ids_found_in_db)} 位演员的缓存信息。")

        # --- 阶段二：为未找到的演员实时查询 Emby API (这部分逻辑不变) ---
        ids_to_fetch_from_api = [pid for pid in original_actor_map.keys() if pid not in ids_found_in_db]
        
        if ids_to_fetch_from_api:
            logger.info(f"  -> 阶段二 (API查询) 开始：为 {len(ids_to_fetch_from_api)} 位新演员实时获取信息...")
            for i, actor_id in enumerate(ids_to_fetch_from_api):
                # ... (这里的 API 调用逻辑保持不变) ...
                full_detail = emby_handler.get_emby_item_details(
                    item_id=actor_id,
                    emby_server_url=self.emby_url,
                    emby_api_key=self.emby_api_key,
                    user_id=self.emby_user_id,
                    fields="ProviderIds,Name" # 只请求最关键的信息
                )
                if full_detail and full_detail.get("ProviderIds"):
                    enriched_actor = original_actor_map[actor_id].copy()
                    enriched_actor["ProviderIds"] = full_detail["ProviderIds"]
                    enriched_actors_map[actor_id] = enriched_actor
                else:
                    logger.warning(f"    未能从 API 获取到演员 ID {actor_id} 的 ProviderIds。")
        else:
            logger.info("  -> 阶段二 (API查询) 跳过：所有演员均在本地数据库中找到。")

        # --- 阶段三：合并最终结果 (这部分逻辑不变) ---
        final_enriched_cast = []
        for original_actor in cast_list:
            actor_id = str(original_actor.get("Id"))
            final_enriched_cast.append(enriched_actors_map.get(actor_id, original_actor))

        return final_enriched_cast
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
    def _get_douban_data_with_local_cache(self, media_info: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[float]]:
        """
        【V3 - 最终版】获取豆瓣数据（演员+评分）。优先本地缓存，失败则回退到功能完整的在线API路径。
        返回: (演员列表, 豆瓣评分) 的元组。
        """
        # 1. 准备查找所需的信息
        provider_ids = media_info.get("ProviderIds", {})
        item_name = media_info.get("Name", "")
        imdb_id = provider_ids.get("Imdb")
        douban_id_from_provider = provider_ids.get("Douban")
        item_type = media_info.get("Type")
        item_year = str(media_info.get("ProductionYear", ""))

        # 2. 尝试从本地缓存查找
        douban_cache_dir_name = "douban-movies" if item_type == "Movie" else "douban-tv"
        douban_cache_path = os.path.join(self.local_data_path, "cache", douban_cache_dir_name)
        local_json_path = self._find_local_douban_json(imdb_id, douban_id_from_provider, douban_cache_path)

        if local_json_path:
            logger.debug(f"  -> 发现本地豆瓣缓存文件，将直接使用: {local_json_path}")
            douban_data = _read_local_json(local_json_path)
            if douban_data:
                cast = douban_data.get('actors', [])
                rating_str = douban_data.get("rating", {}).get("value")
                rating_float = None
                if rating_str:
                    try: rating_float = float(rating_str)
                    except (ValueError, TypeError): pass
                return cast, rating_float
            else:
                logger.warning(f"本地豆瓣缓存文件 '{local_json_path}' 无效，将回退到在线API。")
        
        # 3. 如果本地未找到，回退到功能完整的在线API路径
        logger.info("未找到本地豆瓣缓存，将通过在线API获取演员和评分信息。")

        # 3.1 匹配豆瓣ID和类型。现在 match_info 返回的结果是完全可信的。
        match_info_result = self.douban_api.match_info(
            name=item_name, imdbid=imdb_id, mtype=item_type, year=item_year
        )

        if match_info_result.get("error") or not match_info_result.get("id"):
            logger.warning(f"在线匹配豆瓣ID失败 for '{item_name}': {match_info_result.get('message', '未找到ID')}")
            return [], None

        douban_id = match_info_result["id"]
        # ✨✨✨ 直接信任从 douban.py 返回的类型 ✨✨✨
        douban_type = match_info_result.get("type")

        if not douban_type:
            logger.error(f"从豆瓣匹配结果中未能获取到媒体类型 for ID {douban_id}。处理中止。")
            return [], None

        # 3.2 获取演职员 (使用完全可信的类型)
        cast_data = self.douban_api.get_acting(
            name=item_name, 
            douban_id_override=douban_id, 
            mtype=douban_type
        )
        douban_cast_raw = cast_data.get("cast", [])

        # 3.3 获取详情（为了评分），同样使用可信的类型
        details_data = self.douban_api._get_subject_details(douban_id, douban_type)
        douban_rating = None
        if details_data and not details_data.get("error"):
            rating_str = details_data.get("rating", {}).get("value")
            if rating_str:
                try:
                    douban_rating = float(rating_str)
                    logger.info(f"在线获取到豆瓣评分 for '{item_name}': {douban_rating}")
                except (ValueError, TypeError):
                    pass

        return douban_cast_raw, douban_rating
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
    # --- 通过TmdbID查找映射表 ---
    def _find_person_in_map_by_tmdb_id(self, tmdb_id: str, cursor: sqlite3.Cursor) -> Optional[sqlite3.Row]:
        """
        根据 TMDB ID 在 person_identity_map 表中查找对应的记录。
        """
        if not tmdb_id:
            return None
        try:
            cursor.execute(
                "SELECT * FROM person_identity_map WHERE tmdb_person_id = ?",
                (tmdb_id,)
            )
            return cursor.fetchone()
        except sqlite3.Error as e:
            logger.error(f"通过 TMDB ID '{tmdb_id}' 查询 person_identity_map 时出错: {e}")
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
    # --- 补充新增演员额外数据 ---
    def _get_actor_metadata_from_cache(self, tmdb_id: int, cursor: sqlite3.Cursor) -> Optional[Dict]:
        """根据TMDb ID从ActorMetadata缓存表中获取演员的元数据。"""
        if not tmdb_id:
            return None
        cursor.execute("SELECT * FROM ActorMetadata WHERE tmdb_id = ?", (tmdb_id,))
        metadata_row = cursor.fetchone()  # fetchone() 返回一个 sqlite3.Row 对象或 None
        if metadata_row:
            return dict(metadata_row)  # 将其转换为字典，方便使用
        return None
    # --- 批量注入分集演员表 ---
    def _batch_update_episodes_cast(self, series_id: str, series_name: str, final_cast_list: List[Dict[str, Any]]):
        """
        【V1 - 批量写入模块】
        将一个最终处理好的演员列表，高效地写入指定剧集下的所有分集。
        """
        logger.info(f"  -> 开始为剧集 '{series_name}' (ID: {series_id}) 批量更新所有分集的演员表...")
        
        # 1. 获取所有分集的 ID
        # 我们只需要 ID，所以可以请求更少的字段以提高效率
        episodes = emby_handler.get_series_children(
            series_id=series_id,
            base_url=self.emby_url,
            api_key=self.emby_api_key,
            user_id=self.emby_user_id,
            series_name_for_log=series_name,
            include_item_types="Episode" # ★★★ 明确指定只获取分集
        )
        
        if not episodes:
            logger.info("  -> 未找到任何分集，批量更新结束。")
            return

        total_episodes = len(episodes)
        logger.info(f"  -> 共找到 {total_episodes} 个分集需要更新。")
        
        # 2. 准备好要写入的数据 (所有分集都用同一份演员表)
        cast_for_emby_handler = []
        for actor in final_cast_list:
            cast_for_emby_handler.append({
                "name": actor.get("name"),
                "character": actor.get("character"),
                "emby_person_id": actor.get("emby_person_id"),
                "provider_ids": actor.get("provider_ids")
            })

        # 3. 遍历并逐个更新分集
        # 这里仍然需要逐个更新，因为 Emby API 不支持一次性更新多个项目的演员表
        # 但我们已经把最耗时的数据处理放在了循环外面
        for i, episode in enumerate(episodes):
            if self.is_stop_requested():
                logger.warning("分集批量更新任务被中止。")
                break
            
            episode_id = episode.get("Id")
            episode_name = episode.get("Name", f"分集 {i+1}")
            logger.debug(f"  ({i+1}/{total_episodes}) 正在更新分集 '{episode_name}' (ID: {episode_id})...")
            
            emby_handler.update_emby_item_cast(
                item_id=episode_id,
                new_cast_list_for_handler=cast_for_emby_handler,
                emby_server_url=self.emby_url,
                emby_api_key=self.emby_api_key,
                user_id=self.emby_user_id
            )
            # 加入一个微小的延迟，避免请求过于密集
            time.sleep(0.2)

        logger.info(f"  -> 剧集 '{series_name}' 的分集批量更新完成。")
    # --- 核心处理总管 ---
    def process_single_item(self, emby_item_id: str,
                            force_reprocess_this_item: bool = False,
                            force_fetch_from_tmdb: bool = False):
        """
        【V-API-Ready 最终版 - 带跳过功能】
        这个函数是API模式的入口，它会先检查是否需要跳过已处理的项目。
        """
        # 1. 除非强制，否则跳过已处理的
        if not force_reprocess_this_item and emby_item_id in self.processed_items_cache:
            item_name_from_cache = self.processed_items_cache.get(emby_item_id, f"ID:{emby_item_id}")
            logger.info(f"媒体 '{item_name_from_cache}' 跳过已处理记录。")
            return True

        # 2. 检查停止信号
        if self.is_stop_requested():
            return False

        # 3. 获取Emby详情，这是后续所有操作的基础
        item_details = emby_handler.get_emby_item_details(emby_item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
        if not item_details:
            logger.error(f"process_single_item: 无法获取 Emby 项目 {emby_item_id} 的详情。")
            return False

        # 4. 将任务交给核心处理函数
        return self._process_item_core_logic_api_version(
            item_details_from_emby=item_details,
            force_reprocess_this_item=force_reprocess_this_item,
            force_fetch_from_tmdb=force_fetch_from_tmdb
        )

        # --- 核心处理流程 ---
    
    # ---核心处理流程 ---
    def _process_item_core_logic_api_version(self, item_details_from_emby: Dict[str, Any], force_reprocess_this_item: bool, force_fetch_from_tmdb: bool = False):
        """
        【V-Final Clarity - 清晰最终版】
        确保数据流清晰、单向，并从根源上解决所有已知问题。
        """
        item_id = item_details_from_emby.get("Id")
        item_name_for_log = item_details_from_emby.get("Name", f"未知项目(ID:{item_id})")
        tmdb_id = item_details_from_emby.get("ProviderIds", {}).get("Tmdb")
        item_type = item_details_from_emby.get("Type")

        if not tmdb_id:
            logger.error(f"项目 '{item_name_for_log}' 缺少 TMDb ID，无法处理。")
            return False

        try:
            tmdb_details_for_cache = None
            # ======================================================================
            # 阶段 1: Emby 现状数据准备 
            # ======================================================================
            logger.info(f"  -> 开始处理 '{item_name_for_log}' (TMDb ID: {tmdb_id})")
            
            current_emby_cast_raw = item_details_from_emby.get("People", [])
            enriched_emby_cast = self._enrich_cast_from_db_and_api(current_emby_cast_raw)
            original_emby_actor_count = len(enriched_emby_cast)
            logger.info(f"  -> 从 Emby 获取后，得到 {original_emby_actor_count} 位现有演员用于后续所有操作。")

            # ======================================================================
            # 阶段 2: 权威数据源采集
            # ======================================================================
            authoritative_cast_source = []

            # ★★★★★★★★★★★★★★★ 核心改造：确保总是获取 TMDB 详情 ★★★★★★★★★★★★★★★
            # 无论是什么策略，我们都尝试获取一次 TMDB 详情，以便后续缓存
            if self.tmdb_api_key:
                logger.trace("  -> 实时缓存：正在为补充数据（导演/国家）获取 TMDB 详情...")
                if item_type == "Movie":
                    tmdb_details_for_cache = tmdb_handler.get_movie_details(tmdb_id, self.tmdb_api_key)
                elif item_type == "Series":
                    tmdb_details_for_cache = tmdb_handler.get_tv_details_tmdb(tmdb_id, self.tmdb_api_key)

            # 现在才开始根据策略决定 authoritative_cast_source
            if force_fetch_from_tmdb and tmdb_details_for_cache:
                logger.trace("  -> 策略: 强制刷新，使用刚从 TMDB 获取的数据作为权威数据源。")
                # --- 电影处理逻辑 ---
                if item_type == "Movie":
                    if force_fetch_from_tmdb and self.tmdb_api_key:
                        logger.info("  -> 电影策略: 强制从 TMDB API 获取元数据...")
                        movie_details = tmdb_handler.get_movie_details(tmdb_id, self.tmdb_api_key)
                        if movie_details:
                            credits_data = movie_details.get("credits") or movie_details.get("casts")
                            if credits_data: authoritative_cast_source = credits_data.get("cast", [])
                
                # --- 剧集处理逻辑 ---
                elif item_type == "Series":
                    if force_fetch_from_tmdb and self.tmdb_api_key:
                        logger.info("  -> 剧集策略: 强制从 TMDB API 并发聚合...")
                        aggregated_tmdb_data = tmdb_handler.aggregate_full_series_data_from_tmdb(
                            tv_id=int(tmdb_id), api_key=self.tmdb_api_key, max_workers=5
                        )
                        if aggregated_tmdb_data:
                            all_episodes = list(aggregated_tmdb_data.get("episodes_details", {}).values())
                            authoritative_cast_source = _aggregate_series_cast_from_tmdb_data(aggregated_tmdb_data["series_details"], all_episodes)

            # 如果强制刷新失败，或者没有强制刷新，则使用我们已经增强过的 Emby 列表作为权威数据源
            if not authoritative_cast_source:
                logger.info("  -> 保底策略: 未强制刷新或刷新失败，将使用 Emby 演员列表作为权威数据源。")
                authoritative_cast_source = enriched_emby_cast

            logger.info(f"  -> 演员表采集阶段完成，最终选定 {len(authoritative_cast_source)} 位权威演员。")

            # ======================================================================
            # 阶段 3: 豆瓣及后续处理
            # ======================================================================
            douban_cast_raw, douban_rating = self._get_douban_data_with_local_cache(item_details_from_emby)

            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                
                final_processed_cast = self._process_cast_list_from_api(
                    tmdb_cast_people=authoritative_cast_source,
                    emby_cast_people=enriched_emby_cast,
                    douban_cast_list=douban_cast_raw,
                    item_details_from_emby=item_details_from_emby,
                    cursor=cursor,
                    tmdb_api_key=self.tmdb_api_key,
                    stop_event=self.get_stop_event()
                )

                # ======================================================================
                # 阶段 4: 数据写回 (Data Write-back)
                # ======================================================================
                # --- 步骤 4.1: 前置更新 - 直接更新演员(Person)自身的外部ID和名字 ---
                logger.info("  -> 写回步骤 1/2: 检查并更新演员的元数据...")
                
                # ★★★ 核心修正：不再依赖于电影的原始演员列表进行比较 ★★★
                for actor in final_processed_cast:
                    if self.is_stop_requested():
                        raise InterruptedError("任务在演员元数据更新阶段被中止。")
                    
                    emby_pid = actor.get("emby_person_id")
                    
                    # 只处理在Emby中已存在的演员 (有Emby ID的)
                    if not emby_pid:
                        continue 

                    # 直接构建我们期望的最终数据状态
                    # 即使名字没变，也一起发送，Emby API会处理好
                    data_to_update = {
                        "Name": actor.get("name"),
                        "ProviderIds": actor.get("provider_ids", {})
                    }
                    
                    # 只要这个演员存在于Emby，就调用更新，确保其数据与我们的最终结果一致
                    # 这种做法更健壮，能修复各种不一致的情况
                    logger.trace(f"  -> 准备为演员 '{actor.get('name')}' (ID: {emby_pid}) 同步元数据...")
                    emby_handler.update_person_details(
                        person_id=emby_pid,
                        new_data=data_to_update,
                        emby_server_url=self.emby_url,
                        emby_api_key=self.emby_api_key,
                        user_id=self.emby_user_id
                    )

                logger.info("  -> 演员(Person)元数据前置更新完成。")


                # --- 步骤 4.2: 核心更新 - 更新媒体项目自身的演员列表 (此部分逻辑不变) ---
                logger.info("  -> 写回步骤 2/2: 准备将最终演员列表更新到媒体项目...")
                cast_for_emby_handler = []
                for actor in final_processed_cast:
                    cast_for_emby_handler.append({
                        "name": actor.get("name"),
                        "character": actor.get("character"),
                        "emby_person_id": actor.get("emby_person_id"),
                        "provider_ids": actor.get("provider_ids") 
                    })

                update_success = emby_handler.update_emby_item_cast(
                    item_id=item_id,
                    new_cast_list_for_handler=cast_for_emby_handler,
                    emby_server_url=self.emby_url,
                    emby_api_key=self.emby_api_key,
                    user_id=self.emby_user_id,
                    new_rating=douban_rating
                )

                # ======================================================================
                # ★★★★★★★★★★★★★★★ 阶段 5: 通知Emby刷新完成收尾 ★★★★★★★★★★★★★★★
                # ======================================================================
                # ★★★ 1. 读取您已经存在的、正确的配置开关 ★★★
                auto_refresh_enabled = self.config.get(constants.CONFIG_OPTION_REFRESH_AFTER_UPDATE, True)

                # ★★★ 2. 使用 if 语句包裹整个“刷新”逻辑 ★★★
                if auto_refresh_enabled:
                    auto_lock_enabled = self.config.get(constants.CONFIG_OPTION_AUTO_LOCK_CAST, True)
                    fields_to_lock_on_refresh = ["Cast"] if auto_lock_enabled else None
                    
                    if auto_lock_enabled:
                        logger.info("  -> 更新成功，将执行刷新和锁定操作...")
                    else:
                        logger.info("  -> 更新成功，将执行刷新和解锁操作...")
                        
                    emby_handler.refresh_emby_item_metadata(
                        item_emby_id=item_id,
                        emby_server_url=self.emby_url,
                        emby_api_key=self.emby_api_key,
                        user_id_for_ops=self.emby_user_id,
                        lock_fields=fields_to_lock_on_refresh,
                        replace_all_metadata_param=False,
                        item_name_for_log=item_name_for_log
                    )
                else:
                    # ★★★ 3. 如果禁用了刷新，打印日志告知用户 ★★★
                    logger.info(f"  -> 没有启用自动刷新，跳过刷新和锁定步骤。")

                # ======================================================================
                # 阶段 6: 实时元数据缓存 (现在总是能执行了)
                # ======================================================================
                logger.trace(f"  -> 实时缓存：准备将 '{item_name_for_log}' 的元数据写入本地数据库...")
                _save_metadata_to_cache(
                    cursor=cursor,
                    tmdb_id=tmdb_id,
                    item_type=item_type,
                    item_details_from_emby=item_details_from_emby,
                    final_processed_cast=final_processed_cast,
                    tmdb_details_for_extra=tmdb_details_for_cache # ★ 把我们获取到的补充数据传进去
                )

                # ======================================================================
                # 阶段 7: 后续处理 (Post-processing)
                # ======================================================================
                genres = item_details_from_emby.get("Genres", [])
                is_animation = "Animation" in genres or "动画" in genres or "Documentary" in genres or "纪录" in genres
                processing_score = actor_utils.evaluate_cast_processing_quality(
                    final_cast=final_processed_cast,
                    original_cast_count=original_emby_actor_count,
                    expected_final_count=len(final_processed_cast),
                    is_animation=is_animation
                )

                min_score_for_review = float(self.config.get("min_score_for_review", constants.DEFAULT_MIN_SCORE_FOR_REVIEW))
                if processing_score < min_score_for_review:
                    reason = f"处理评分 ({processing_score:.2f}) 低于阈值 ({min_score_for_review})。"
                    self.log_db_manager.save_to_failed_log(cursor, item_id, item_name_for_log, reason, item_type, score=processing_score)
                else:
                    self.log_db_manager.save_to_processed_log(cursor, item_id, item_name_for_log, score=processing_score)
                    self.log_db_manager.remove_from_failed_log(cursor, item_id)
                    self.processed_items_cache[item_id] = item_name_for_log
                    logger.debug(f"已将 '{item_name_for_log}' (ID: {item_id}) 添加到已处理，下次将跳过。")

                conn.commit()

        except (ValueError, InterruptedError) as e:
            logger.warning(f"处理 '{item_name_for_log}' 的过程中断: {e}")
            return False
        except Exception as outer_e:
            logger.error(f"API模式核心处理流程中发生未知严重错误 for '{item_name_for_log}': {outer_e}", exc_info=True)
            try:
                with get_central_db_connection(self.db_path) as conn_fail:
                    self.log_db_manager.save_to_failed_log(conn_fail.cursor(), item_id, item_name_for_log, f"核心处理异常: {str(outer_e)}", item_type)
            except Exception as log_e:
                logger.error(f"写入失败日志时再次发生错误: {log_e}")
            return False

        logger.info(f"✨✨✨ 处理完成 '{item_name_for_log}' ✨✨✨")
        return True

    # --- 核心处理器 ---
    def _process_cast_list_from_api(self, tmdb_cast_people: List[Dict[str, Any]],
                                    emby_cast_people: List[Dict[str, Any]],
                                    douban_cast_list: List[Dict[str, Any]],
                                    item_details_from_emby: Dict[str, Any],
                                    cursor: sqlite3.Cursor,
                                    tmdb_api_key: Optional[str],
                                    stop_event: Optional[threading.Event]) -> List[Dict[str, Any]]:
        """
        在函数开头增加一个“数据适配层”，将API数据转换为你现有逻辑期望的格式，
        """
        # ======================================================================
        # 步骤 1: ★★★ 数据适配 ★★★
        # ======================================================================
        logger.debug("  -> 开始演员数据适配...")
        # 1. 创建一个基于当前电影演员的临时映射 (保持不变)
        emby_tmdb_to_person_id_map = {
            person.get("ProviderIds", {}).get("Tmdb"): person.get("Id")
            for person in emby_cast_people if person.get("ProviderIds", {}).get("Tmdb")
        }
        local_cast_list = []
        for person_data in tmdb_cast_people: # tmdb_cast_people 现在是 authoritative_cast_source
            
            tmdb_id = None
            if "id" in person_data:
                tmdb_id = str(person_data.get("id"))
            elif "ProviderIds" in person_data and person_data.get("ProviderIds", {}).get("Tmdb"):
                tmdb_id = str(person_data["ProviderIds"]["Tmdb"])
            
            if not tmdb_id or tmdb_id == 'None':
                continue

            new_actor_entry = person_data.copy()
            
            # 2. 优先从临时映射中获取 emby_person_id
            emby_pid = emby_tmdb_to_person_id_map.get(tmdb_id)
            
            # 3. 如果临时映射中没有（说明这个演员不是当前电影的成员），则查询全局数据库
            if not emby_pid:
                db_entry_row = self._find_person_in_map_by_tmdb_id(tmdb_id, cursor)
                # 2. 立即将其转换为标准的 dict 字典
                db_entry = dict(db_entry_row) if db_entry_row else None
                if db_entry and db_entry.get("emby_person_id"):
                    emby_pid = db_entry["emby_person_id"]
                    logger.trace(f"  -> 为演员 '{new_actor_entry.get('name')}' (TMDB ID: {tmdb_id}) 从全局数据库中找到了 Emby Person ID: {emby_pid}")

            # 4. 将最终找到的ID（可能是None）注入
            new_actor_entry["emby_person_id"] = emby_pid
            
            # 统一数据结构 (保持不变)
            if "id" not in new_actor_entry: new_actor_entry["id"] = tmdb_id
            if "name" not in new_actor_entry: new_actor_entry["name"] = new_actor_entry.get("Name")
            if "character" not in new_actor_entry: new_actor_entry["character"] = new_actor_entry.get("Role")

            local_cast_list.append(new_actor_entry)
        
        logger.debug(f"  -> 数据适配完成，生成了 {len(local_cast_list)} 条基准演员数据。")
        # ======================================================================
        # 步骤 2: ★★★ “一对一匹配”逻辑 ★★★
        # ======================================================================

        douban_candidates = actor_utils.format_douban_cast(douban_cast_list)

        unmatched_local_actors = list(local_cast_list)  # ★★★ 使用我们适配好的数据源 ★★★
        merged_actors = []
        unmatched_douban_actors = []
        #  遍历豆瓣演员，尝试在“未匹配”的本地演员中寻找配对
        logger.debug(f" --- 匹配阶段 1: 对号入座 ---")
        for d_actor in douban_candidates:
            douban_name_zh = d_actor.get("Name", "").lower().strip()
            douban_name_en = d_actor.get("OriginalName", "").lower().strip()

            match_found_for_this_douban_actor = False
            
            for i, l_actor in enumerate(unmatched_local_actors):
                local_name = str(l_actor.get("name") or "").lower().strip()
                local_original_name = str(l_actor.get("original_name") or "").lower().strip()
                is_match, match_reason = False, ""
                if douban_name_zh and (douban_name_zh == local_name or douban_name_zh == local_original_name):
                    is_match, match_reason = True, "精确匹配 (豆瓣中文名)"
                elif douban_name_en and (douban_name_en == local_name or douban_name_en == local_original_name):
                    is_match, match_reason = True, "精确匹配 (豆瓣外文名)"
                
                if is_match:
                    logger.debug(f"  -> 匹配成功： (对号入座): 豆瓣演员 '{d_actor.get('Name')}' -> 本地演员 '{l_actor.get('name')}' (ID: {l_actor.get('id')})")

                    l_actor["name"] = d_actor.get("Name")
                    cleaned_douban_character = utils.clean_character_name_static(d_actor.get("Role"))
                    l_actor["character"] = actor_utils.select_best_role(l_actor.get("character"), cleaned_douban_character)
                    if d_actor.get("DoubanCelebrityId"):
                        l_actor["douban_id"] = d_actor.get("DoubanCelebrityId")

                    merged_actors.append(unmatched_local_actors.pop(i))
                    match_found_for_this_douban_actor = True
                    break

            if not match_found_for_this_douban_actor:
                unmatched_douban_actors.append(d_actor)

        # 这里先把旧演员合并成列表，供后续新增和处理使用
        current_cast_list = merged_actors + unmatched_local_actors

        # 先构造 final_cast_map，包含旧演员
        final_cast_map = {str(actor['id']): actor for actor in current_cast_list if actor.get('id') and str(actor.get('id')) != 'None'}
        # 新增阶段开始
        limit = self.config.get(constants.CONFIG_OPTION_MAX_ACTORS_TO_PROCESS, 30)
        try:
            limit = int(limit)
            if limit <= 0:
                limit = 30
        except (ValueError, TypeError):
            limit = 30

        current_actor_count = len(final_cast_map)
        if current_actor_count >= limit:
            logger.info(f"  -> 当前演员数 ({current_actor_count}) 已达上限 ({limit})，跳过所有新增演员的流程。")
        else:
            logger.info(f"  -> 当前演员数 ({current_actor_count}) 低于上限 ({limit})，进入补充模式（处理来自豆瓣的新增演员）。")

            logger.debug(f" --- 匹配阶段 2: 用豆瓣ID查'演员映射表' ({len(unmatched_douban_actors)} 位演员) ---")
            still_unmatched = []
            for d_actor in unmatched_douban_actors:
                if self.is_stop_requested():
                    raise InterruptedError("任务中止")
                d_douban_id = d_actor.get("DoubanCelebrityId")
                match_found = False
                if d_douban_id:
                    # 1. 从数据库获取 sqlite3.Row 对象
                    entry_row = self._find_person_in_map_by_douban_id(d_douban_id, cursor)
                    
                    # ★★★★★★★★★★★★★★★ 终极修复：将 Row 转换为 Dict ★★★★★★★★★★★★★★★
                    entry = dict(entry_row) if entry_row else None

                    if entry and entry.get("tmdb_person_id"):
                        tmdb_id_from_map = str(entry.get("tmdb_person_id"))
                        if tmdb_id_from_map not in final_cast_map:
                            logger.debug(f"  -> 匹配成功 (通过 豆瓣ID映射): 豆瓣演员 '{d_actor.get('Name')}' -> 加入最终演员表")
                            cached_metadata = self._get_actor_metadata_from_cache(tmdb_id_from_map, cursor) or {}
                            new_actor_entry = {
                                "id": tmdb_id_from_map,
                                "name": d_actor.get("Name"),
                                "original_name": cached_metadata.get("original_name") or d_actor.get("OriginalName"),
                                "character": d_actor.get("Role"),
                                "adult": cached_metadata.get("adult", False),
                                "gender": cached_metadata.get("gender", 0),
                                "known_for_department": "Acting",
                                "popularity": cached_metadata.get("popularity", 0.0),
                                "profile_path": cached_metadata.get("profile_path"),
                                "cast_id": None,
                                "credit_id": None,
                                "order": 999,
                                "imdb_id": entry.get("imdb_id"),
                                "douban_id": d_douban_id,
                                "emby_person_id": entry.get("emby_person_id"),
                                "_is_newly_added": True
                            }
                            final_cast_map[tmdb_id_from_map] = new_actor_entry
                        match_found = True
                if not match_found:
                    still_unmatched.append(d_actor)
            unmatched_douban_actors = still_unmatched

            # ======================================================================
            # 步骤 3: ★★★ 映射表匹配以及Tmdb反查 ★★★
            # ======================================================================
            logger.debug(f" --- 匹配阶段 3: 用IMDb ID进行最终匹配和新增 ({len(unmatched_douban_actors)} 位演员) ---")
            still_unmatched_final = []
            for i, d_actor in enumerate(unmatched_douban_actors):
                if self.is_stop_requested():
                    raise InterruptedError("任务中止")

                if len(final_cast_map) >= limit:
                    logger.info(f"  -> 演员数已达上限 ({limit})，跳过剩余 {len(unmatched_douban_actors) - i} 位演员的API查询。")
                    still_unmatched_final.extend(unmatched_douban_actors[i:])
                    break
                d_douban_id = d_actor.get("DoubanCelebrityId")
                match_found = False
                if d_douban_id and self.douban_api and self.tmdb_api_key:
                    if self.is_stop_requested():
                        logger.info("  -> 任务在处理豆瓣演员时被中止 (豆瓣API调用前)。")
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
                            logger.warning(f"  -> 解析 IMDb ID 时发生意外错误: {e_parse}")

                    if d_imdb_id:
                        logger.debug(f"  -> 为 '{d_actor.get('Name')}' 获取到 IMDb ID: {d_imdb_id}，开始匹配...")

                        entry_row_from_map = self._find_person_in_map_by_imdb_id(d_imdb_id, cursor)
                        
                        # ★★★★★★★★★★★★★★★ 终极修复：将 Row 转换为 Dict ★★★★★★★★★★★★★★★
                        entry_from_map = dict(entry_row_from_map) if entry_row_from_map else None

                        if entry_from_map and entry_from_map.get("tmdb_person_id"):
                            tmdb_id_from_map = str(entry_from_map.get("tmdb_person_id"))

                            if tmdb_id_from_map not in final_cast_map:
                                logger.debug(f"  -> 匹配成功 (通过 IMDb映射): 豆瓣演员 '{d_actor.get('Name')}' -> 加入最终演员表")
                                cached_metadata = self._get_actor_metadata_from_cache(tmdb_id_from_map, cursor) or {}
                                new_actor_entry = {
                                    "id": tmdb_id_from_map,
                                    "name": d_actor.get("Name"),
                                    "original_name": cached_metadata.get("original_name") or d_actor.get("OriginalName"),
                                    "character": d_actor.get("Role"),
                                    "order": 999,
                                    "imdb_id": d_imdb_id,
                                    "douban_id": d_douban_id,
                                    "emby_person_id": entry_from_map.get("emby_person_id"), # 现在可以安全地使用 .get()
                                    "_is_newly_added": True
                                }
                                final_cast_map[tmdb_id_from_map] = new_actor_entry

                            logger.debug(f"  -> [实时反哺] 将新发现的映射关系 (Douban ID: {d_douban_id}) 保存回演员映射表...")
                            self.actor_db_manager.upsert_person(
                                cursor,
                                {
                                    "tmdb_id": tmdb_id_from_map,
                                    "imdb_id": d_imdb_id,
                                    "douban_id": d_douban_id,
                                    "name": d_actor.get("Name") or (entry_from_map["primary_name"] if "primary_name" in entry_from_map else None)
                                }
                            )
                            match_found = True

                        if not match_found:
                            logger.debug(f"  -> 数据库未找到 {d_imdb_id} 的映射，开始通过 TMDb API 反查...")
                            if self.is_stop_requested():
                                logger.info("  -> 任务在处理豆瓣演员时被中止 (TMDb API调用前)。")
                                raise InterruptedError("任务中止")

                            # 1. 默认使用豆瓣提供的外文名作为后备
                            name_for_verification = d_actor.get("OriginalName")
                            log_source = "豆瓣"

                            # 2. 尝试从可能存在的、不完整的本地映射中获取更权威的名字
                            if entry_from_map and entry_from_map.get("tmdb_person_id"):
                                tmdb_id_from_map = str(entry_from_map.get("tmdb_person_id"))
                                # 使用这个 tmdb_id 去查询元数据缓存
                                cached_metadata = self._get_actor_metadata_from_cache(tmdb_id_from_map, cursor)
                                if cached_metadata and cached_metadata.get("original_name"):
                                    # 如果成功获取，就覆盖掉来自豆瓣的名字
                                    name_for_verification = cached_metadata.get("original_name")
                                    log_source = "本地数据库"
                                    logger.debug(f"  -> [验证准备] 成功从本地数据库为 TMDb ID {tmdb_id_from_map} 找到用于验证的 original_name: '{name_for_verification}'")

                            logger.debug(f"  -> 将使用来自 [{log_source}] 的外文名 '{name_for_verification}' 进行 TMDb API 匹配验证。")

                            names_to_verify = {
                                "chinese_name": d_actor.get("Name"),
                                "original_name": name_for_verification 
                            }
                            
                            person_from_tmdb = tmdb_handler.find_person_by_external_id(
                                external_id=d_imdb_id, 
                                api_key=self.tmdb_api_key, 
                                source="imdb_id",
                                names_for_verification=names_to_verify
                            )
                            
                            if person_from_tmdb and person_from_tmdb.get("id"):
                                tmdb_id_from_find = str(person_from_tmdb.get("id"))

                                if tmdb_id_from_find not in final_cast_map:
                                    logger.debug(f"  -> 匹配成功 (通过 TMDb反查): 豆瓣演员 '{d_actor.get('Name')}' -> 加入最终演员表")
                                    # 用新找到的TMDB ID，对本地数据库进行最后一次检查，看是否已有关联的Emby ID
                                    emby_pid_from_final_check = None
                                    final_check_row = self._find_person_in_map_by_tmdb_id(tmdb_id_from_find, cursor)
                                    if final_check_row:
                                        final_check_entry = dict(final_check_row)
                                        emby_pid_from_final_check = final_check_entry.get("emby_person_id")
                                        if emby_pid_from_final_check:
                                            logger.trace(f"  -> [最终检查] 发现该TMDB ID已关联Emby Person ID: {emby_pid_from_final_check}")
                                    cached_metadata = self._get_actor_metadata_from_cache(tmdb_id_from_find, cursor) or {}
                                    new_actor_entry = {
                                        "id": tmdb_id_from_find,
                                        "name": d_actor.get("Name"),
                                        "original_name": cached_metadata.get("original_name") or d_actor.get("OriginalName"),
                                        "character": d_actor.get("Role"),
                                        "adult": cached_metadata.get("adult", False),
                                        "gender": cached_metadata.get("gender", 0),
                                        "known_for_department": "Acting",
                                        "popularity": cached_metadata.get("popularity", 0.0),
                                        "profile_path": cached_metadata.get("profile_path"),
                                        "cast_id": None,
                                        "credit_id": None,
                                        "order": 999,
                                        "imdb_id": d_imdb_id,
                                        "douban_id": d_douban_id,
                                        "emby_person_id": emby_pid_from_final_check,
                                        "_is_newly_added": True
                                    }
                                    final_cast_map[tmdb_id_from_find] = new_actor_entry
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
                logger.info(f"  -> 最终丢弃 {len(still_unmatched_final)} 位豆瓣演员 ---")
            unmatched_douban_actors = still_unmatched_final

        # 将最终演员列表取自 final_cast_map，包含所有旧＋新演员
        current_cast_list = list(final_cast_map.values())

        # ★★★ 在截断前进行一次全量反哺映射表 ★★★
        logger.debug(f"  -> 截断前：将 {len(current_cast_list)} 位演员的完整映射关系反哺到数据库...")
        for actor_data in current_cast_list:
            self.actor_db_manager.upsert_person(
                cursor,
                {
                    "tmdb_id": actor_data.get("id"),
                    "name": actor_data.get("name"),
                    "imdb_id": actor_data.get("imdb_id"),
                    "douban_id": actor_data.get("douban_id"),
                },
            )
        logger.trace("  -> 所有演员的ID映射关系已保存。")

        # 演员列表截断 (先截断！)
        max_actors = self.config.get(constants.CONFIG_OPTION_MAX_ACTORS_TO_PROCESS, 30)
        try:
            limit = int(max_actors)
            if limit <= 0:
                limit = 30
        except (ValueError, TypeError):
            limit = 30

        original_count = len(current_cast_list)
        if original_count > limit:
            logger.info(f"  -> 演员列表总数 ({original_count}) 超过上限 ({limit})，将在翻译前进行截断。")
            # 按 order 排序
            current_cast_list.sort(key=lambda x: x.get('order') if x.get('order') is not None and x.get('order') >= 0 else 999)
            cast_to_process = current_cast_list[:limit]
        else:
            cast_to_process = current_cast_list

        logger.info(f"  -> 将对 {len(cast_to_process)} 位演员进行最终的翻译和格式化处理...")

        # ======================================================================
        # 步骤 4: 翻译准备与执行 (后收集，并检查缓存！)
        # ======================================================================
        ai_translation_succeeded = False
        translation_cache = {}  # ★★★ 核心修正1：将缓存初始化在最外面
        texts_to_collect = set()
        texts_to_send_to_api = set()

        if self.ai_translator and self.config.get(constants.CONFIG_OPTION_AI_TRANSLATION_ENABLED, False):
            logger.info("  -> AI翻译已启用，优先尝试批量翻译模式。")

            try:
                translation_mode = self.config.get(constants.CONFIG_OPTION_AI_TRANSLATION_MODE, "fast")

                for actor in cast_to_process:
                    name = actor.get('name')
                    if name and not utils.contains_chinese(name):
                        texts_to_collect.add(name)

                    character = actor.get('character')
                    if character:
                        cleaned_character = utils.clean_character_name_static(character)
                        if cleaned_character and not utils.contains_chinese(cleaned_character):
                            texts_to_collect.add(cleaned_character)

                if translation_mode == 'fast':
                    logger.debug("  -> [翻译模式] 正在检查全局翻译缓存...")
                    for text in texts_to_collect:
                        cached_entry = self.actor_db_manager.get_translation_from_db(cursor=cursor, text=text)
                        if cached_entry:
                            translation_cache[text] = cached_entry.get("translated_text")
                        else:
                            texts_to_send_to_api.add(text)
                else:
                    logger.debug("  -> [顾问模式] 跳过缓存检查，直接翻译所有词条。")
                    texts_to_send_to_api = texts_to_collect
                if texts_to_send_to_api:
                    item_title = item_details_from_emby.get("Name")
                    item_year = item_details_from_emby.get("ProductionYear")

                    logger.info(f"  -> 将 {len(texts_to_send_to_api)} 个词条提交给AI (模式: {translation_mode})。")

                    translation_map_from_api = self.ai_translator.batch_translate(
                        texts=list(texts_to_send_to_api),
                        mode=translation_mode,
                        title=item_title,
                        year=item_year
                    )

                    if translation_map_from_api:
                        translation_cache.update(translation_map_from_api)
                        if translation_mode == 'fast':
                            for original, translated in translation_map_from_api.items():
                                self.actor_db_manager.save_translation_to_db(
                                    cursor=cursor,
                                    original_text=original,
                                    translated_text=translated,
                                    engine_used=self.ai_translator.provider
                                )

                ai_translation_succeeded = True
            except Exception as e:
                logger.error(f"  -> 调用AI批量翻译时发生严重错误: {e}", exc_info=True)
                ai_translation_succeeded = False
        else:
            logger.info("  -> AI翻译未启用，将保留演员和角色名原文。")

        # --- ★★★ 核心修正2：无论AI是否成功，都执行清理与回填，降级逻辑只在AI失败时触发 ★★★

        if ai_translation_succeeded:
            logger.info("------------ AI翻译流程成功，开始应用结果 ------------")

            if not texts_to_collect:
                logger.info("  所有演员名和角色名均已是中文，无需翻译。")
            elif not texts_to_send_to_api:
                logger.info(f"  所有 {len(texts_to_collect)} 个待翻译词条均从数据库缓存中获取，无需调用AI。")
            else:
                logger.info(f"  AI翻译完成，共处理 {len(translation_cache)} 个词条。")

            # 无条件执行回填，因为translation_cache包含所有需数据（来自缓存或API）。
            for actor in cast_to_process:
                # 1. 处理演员名
                original_name = actor.get('name')
                translated_name = translation_cache.get(original_name, original_name)
                if original_name != translated_name:
                    logger.debug(f"  演员名翻译: '{original_name}' -> '{translated_name}'")
                actor['name'] = translated_name

                # 2. 处理角色名
                original_character = actor.get('character')
                if original_character:
                    cleaned_character = utils.clean_character_name_static(original_character)
                    translated_character = translation_cache.get(cleaned_character, cleaned_character)
                    if translated_character != original_character:
                        actor_name_for_log = actor.get('name', '未知演员')
                        logger.debug(f"  角色名翻译: '{original_character}' -> '{translated_character}' (演员: {actor_name_for_log})")
                    actor['character'] = translated_character
                else:
                    # 保证字段始终有字符串，避免漏网
                    actor['character'] = ''

            logger.info("----------------------------------------------------")
        else:
            # AI失败时保留原文，不做翻译改写
            if self.config.get(constants.CONFIG_OPTION_AI_TRANSLATION_ENABLED, False):
                logger.warning("  -> AI批量翻译失败，将保留演员和角色名原文。")

        # ======================================================================
        # 步骤 5: 格式化最终演员表
        # ======================================================================
        # 5.1: 在调用黑盒函数前，备份所有 emby_person_id
        logger.trace("格式化前：备份 emby_person_id...")
        tmdb_to_emby_id_map = {
            str(actor.get('id')): actor.get('emby_person_id')
            for actor in cast_to_process if actor.get('id') and actor.get('emby_person_id')
        }
        logger.trace(f"已备份 {len(tmdb_to_emby_id_map)} 个 Emby Person ID 映射。")
        
        # 5.2: 正常调用格式化函数 (黑盒)
        logger.trace("调用 actor_utils.format_and_complete_cast_list 进行格式化...")
        is_animation = "Animation" in item_details_from_emby.get("Genres", [])
        final_cast_perfect = actor_utils.format_and_complete_cast_list(
            cast_to_process, is_animation, self.config, mode='auto'
        )

        # 5.3: 格式化后，将备份的 emby_person_id 重新注入
        logger.trace("格式化后：恢复 emby_person_id...")
        restored_count = 0
        for actor in final_cast_perfect:
            tmdb_id_str = str(actor.get("id"))
            if tmdb_id_str in tmdb_to_emby_id_map:
                actor["emby_person_id"] = tmdb_to_emby_id_map[tmdb_id_str]
                restored_count += 1
        logger.trace(f"已为 {restored_count} 位演员恢复了 Emby Person ID。")

        # 5.4: 准备最终的 provider_ids
        logger.trace("准备最终的 provider_ids...")
        for actor in final_cast_perfect:
            actor["provider_ids"] = {
                "Tmdb": str(actor.get("id")),
                "Imdb": actor.get("imdb_id"),
                "Douban": actor.get("douban_id")
            }
            if actor.get("emby_person_id"):
                logger.trace(f"  演员 '{actor.get('name')}' 最终保留了 Emby Person ID: {actor.get('emby_person_id')}")

        return final_cast_perfect

    def process_full_library(self, update_status_callback: Optional[callable] = None, force_reprocess_all: bool = False, force_fetch_from_tmdb: bool = False):
        """
        【V3 - 最终完整版】
        这是所有全量处理的唯一入口，它自己处理所有与“强制”相关的逻辑。
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
                                    year: Optional[int] = None,
                                    tmdb_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        【V13 - 返璞归真双核版】为手动编辑页面提供的一键翻译功能。
        根据用户配置，智能选择带全局缓存的翻译模式，或无缓存的顾问模式。
        """
        if not cast_list:
            return []
            
        # 从配置中读取模式，这是决定后续所有行为的总开关
        translation_mode = self.config.get(constants.CONFIG_OPTION_AI_TRANSLATION_MODE, "fast")
        
        context_log = f" (上下文: {title} {year})" if title and translation_mode == 'quality' else ""
        logger.info(f"手动编辑-一键翻译：开始批量处理 {len(cast_list)} 位演员 (模式: {translation_mode}){context_log}。")
        
        translated_cast = [dict(actor) for actor in cast_list]
        
        # --- 批量翻译逻辑 ---
        ai_translation_succeeded = False
        
        if self.ai_translator and self.config.get(constants.CONFIG_OPTION_AI_TRANSLATION_ENABLED, False):
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                
                translation_cache = {} # 本次运行的内存缓存
                texts_to_translate = set()

                # 1. 收集所有需要翻译的词条
                texts_to_collect = set()
                for actor in translated_cast:
                    for field_key in ['name', 'role']:
                        text = actor.get(field_key, '').strip()
                        if field_key == 'role':
                            # 无论是演员名还是角色名，都先清洗一遍，确保拿到的是核心文本
                            # 对于演员名，这个清洗通常无影响，但对于角色名至关重要
                            text = utils.clean_character_name_static(text)
                        if text and not utils.contains_chinese(text):
                            texts_to_collect.add(text)

                # 2. 根据模式决定是否使用缓存
                if translation_mode == 'fast':
                    logger.debug("[翻译模式] 正在检查全局翻译缓存...")
                    for text in texts_to_collect:
                        # 翻译模式只读写全局缓存
                        cached_entry = self.actor_db_manager.get_translation_from_db(cursor=cursor, text=text)
                        if cached_entry:
                            translation_cache[text] = cached_entry.get("translated_text")
                        else:
                            texts_to_translate.add(text)
                else: # 'quality' mode
                    logger.debug("[顾问模式] 跳过缓存检查，直接翻译所有词条。")
                    texts_to_translate = texts_to_collect

                # 3. 如果有需要翻译的词条，调用AI
                if texts_to_translate:
                    logger.info(f"手动编辑-翻译：将 {len(texts_to_translate)} 个词条提交给AI (模式: {translation_mode})。")
                    try:
                        translation_map_from_api = self.ai_translator.batch_translate(
                            texts=list(texts_to_translate),
                            mode=translation_mode,
                            title=title,
                            year=year
                        )
                        if translation_map_from_api:
                            translation_cache.update(translation_map_from_api)
                            
                            # 只有在翻译模式下，才将结果写入全局缓存
                            if translation_mode == 'fast':
                                for original, translated in translation_map_from_api.items():
                                    self.actor_db_manager.save_translation_to_db(
                                        cursor=cursor,
                                        original_text=original, 
                                        translated_text=translated, 
                                        engine_used=self.ai_translator.provider
                                    )
                            
                            ai_translation_succeeded = True
                        else:
                            logger.warning("手动编辑-翻译：AI批量翻译未返回结果。")
                    except Exception as e:
                        logger.error(f"手动编辑-翻译：调用AI批量翻译时出错: {e}", exc_info=True)
                else:
                    logger.info("手动编辑-翻译：所有词条均在缓存中找到，无需调用API。")
                    ai_translation_succeeded = True

                # 4. 回填所有翻译结果
                if translation_cache:
                    for i, actor in enumerate(translated_cast):
                        original_name = actor.get('name', '').strip()
                        if original_name in translation_cache:
                            translated_cast[i]['name'] = translation_cache[original_name]
                        
                        original_role_raw = actor.get('role', '').strip()
                        # 使用与收集时完全相同的清理逻辑
                        cleaned_original_role = utils.clean_character_name_static(original_role_raw)
                        
                        # 用清理后的名字作为key去查找
                        if cleaned_original_role in translation_cache:
                            translated_cast[i]['role'] = translation_cache[cleaned_original_role]
                        
                        # 如果发生了翻译，更新状态以便前端高亮
                        if translated_cast[i].get('name') != actor.get('name') or translated_cast[i].get('role') != actor.get('role'):
                            translated_cast[i]['matchStatus'] = '已翻译'
        
        # 如果AI翻译未启用或失败，则降级到传统引擎
        if not ai_translation_succeeded:
            if self.config.get("ai_translation_enabled", False):
                logger.info("手动编辑-翻译：AI翻译失败，降级到传统引擎逐个翻译。")
            else:
                logger.info("手动编辑-翻译：AI未启用，使用传统引擎逐个翻译。")
                
            try:
                with get_central_db_connection(self.db_path) as conn:
                    cursor = conn.cursor()

                    for i, actor in enumerate(translated_cast):
                        if self.is_stop_requested():
                            logger.warning(f"一键翻译（降级模式）被用户中止。")
                            break # 这里使用 break 更安全，可以直接跳出循环
                        # 【【【 修复点 3：使用正确的参数调用 translate_actor_field 】】】
                        
                        # 翻译演员名
                        name_to_translate = actor.get('name', '').strip()
                        if name_to_translate and not utils.contains_chinese(name_to_translate):
                            translated_name = actor_utils.translate_actor_field(
                                text=name_to_translate,
                                db_manager=self.actor_db_manager,
                                db_cursor=cursor,
                                ai_translator=self.ai_translator,
                                translator_engines=self.translator_engines,
                                ai_enabled=self.ai_enabled
                            )
                            if translated_name and translated_name != name_to_translate:
                                translated_cast[i]['name'] = translated_name

                        # 翻译角色名
                        role_to_translate = actor.get('role', '').strip()
                        if role_to_translate and not utils.contains_chinese(role_to_translate):
                            translated_role = actor_utils.translate_actor_field(
                                text=role_to_translate,
                                db_manager=self.actor_db_manager,
                                db_cursor=cursor,
                                ai_translator=self.ai_translator,
                                translator_engines=self.translator_engines,
                                ai_enabled=self.ai_enabled
                            )
                            if translated_role and translated_role != role_to_translate:
                                translated_cast[i]['role'] = translated_role

                        if translated_cast[i].get('name') != actor.get('name') or translated_cast[i].get('role') != actor.get('role'):
                            translated_cast[i]['matchStatus'] = '已翻译'
            
            except Exception as e:
                logger.error(f"一键翻译（降级模式）时发生错误: {e}", exc_info=True)

        logger.info("手动编辑-翻译完成。")
        return translated_cast
    # ✨✨✨手动处理✨✨✨
    # core_processor.py

# ... (文件其他部分保持不变) ...

    # ✨✨✨手动处理✨✨✨
    def process_item_with_manual_cast(self, item_id: str, manual_cast_list: List[Dict[str, Any]], item_name: str) -> bool:
        """
        【V-API-Direct - 功能增强最终版】
        完全信任前端提交的演员列表，直接写入Emby，并集成了翻译缓存更新和剧集分集处理功能。
        """
        logger.info(f"  -> 手动处理流程启动：ItemID: {item_id} ('{item_name}')")
        
        try:
            # ======================================================================
            # 步骤 1: 数据准备与转换
            # ======================================================================
            logger.debug(f"接收到前端提交的 {len(manual_cast_list)} 位演员数据，开始转换...")
            
            # 1.1 获取媒体详情，这是后续所有操作的基础
            item_details = emby_handler.get_emby_item_details(item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
            if not item_details:
                raise ValueError(f"无法获取项目 {item_id} 的详情。")
            item_type = item_details.get("Type")

            # 1.2 将前端数据转换为 Emby Handler 需要的格式
            cast_for_emby_handler = []
            for actor in manual_cast_list:
                emby_pid = actor.get("emby_person_id")
                if not emby_pid:
                    logger.warning(f"跳过演员 '{actor.get('name')}'，因为缺少 emby_person_id。")
                    continue
                cast_for_emby_handler.append({
                    "name": actor.get("name"),
                    "character": actor.get("role"),
                    "emby_person_id": emby_pid,
                    "provider_ids": {"Tmdb": actor.get("tmdbId")}
                })

            # ======================================================================
            # ★★★ 新增功能 1: 更新翻译缓存 ★★★
            # ======================================================================
            try:
                # 1. 从内存缓存中获取这个会话的完整原始演员列表
                original_full_cast = self.manual_edit_cache.get(item_id)
                if original_full_cast:
                    # 构建一个以 emby_person_id 为键的原始数据映射表
                    original_cast_map = {str(actor.get('emby_person_id')): actor for actor in original_full_cast}
                    
                    with get_central_db_connection(self.db_path) as conn:
                        cursor = conn.cursor()
                        cursor.execute("BEGIN TRANSACTION;")
                        try:
                            for actor_from_frontend in manual_cast_list:
                                emby_pid = actor_from_frontend.get("emby_person_id")
                                if not emby_pid: continue
                                
                                original_actor_data = original_cast_map.get(str(emby_pid))
                                if not original_actor_data: continue

                                new_role = actor_from_frontend.get('role', '')
                                original_role = original_actor_data.get('character', '')
                                
                                # 只有当角色名发生变化时才尝试更新缓存
                                if new_role != original_role:
                                    cleaned_original_role = utils.clean_character_name_static(original_role)
                                    cleaned_new_role = utils.clean_character_name_static(new_role)
                                    
                                    if cleaned_new_role and cleaned_new_role != cleaned_original_role:
                                        cache_entry = self.actor_db_manager.get_translation_from_db(
                                            text=cleaned_original_role, by_translated_text=True, cursor=cursor
                                        )
                                        if cache_entry and 'original_text' in cache_entry:
                                            original_text_key = cache_entry['original_text']
                                            self.actor_db_manager.save_translation_to_db(
                                                cursor=cursor,
                                                original_text=original_text_key,
                                                translated_text=cleaned_new_role,
                                                engine_used="manual"
                                            )
                                            logger.debug(f"  -> AI缓存通过反查更新: '{original_text_key}' -> '{cleaned_new_role}'")
                            conn.commit()
                        except Exception as e_cache:
                            logger.error(f"更新翻译缓存事务中发生错误: {e_cache}", exc_info=True)
                            conn.rollback()
                else:
                    logger.warning(f"无法更新翻译缓存，因为在内存中找不到 ItemID {item_id} 的原始演员数据。")
            except Exception as e:
                logger.error(f"手动处理期间更新翻译缓存时发生顶层错误: {e}", exc_info=True)


            # ======================================================================
            # 步骤 2: 执行“两步更新”到 Emby
            # ======================================================================
            
            # 2.1: 前置更新演员名
            logger.info("  -> 手动处理：步骤 1/2: 检查并更新演员名字...")
            original_names_map = {p.get("Id"): p.get("Name") for p in item_details.get("People", []) if p.get("Id")}
            for actor in cast_for_emby_handler:
                actor_id = actor.get("emby_person_id")
                new_name = actor.get("name")
                original_name = original_names_map.get(actor_id)
                if actor_id and new_name and original_name and new_name != original_name:
                    logger.info(f"  -> 检测到手动名字变更，正在更新 Person: '{original_name}' -> '{new_name}' (ID: {actor_id})")
                    emby_handler.update_person_details(
                        person_id=actor_id, new_data={"Name": new_name},
                        emby_server_url=self.emby_url, emby_api_key=self.emby_api_key, user_id=self.emby_user_id
                    )
            logger.info("  -> 手动处理：演员名字前置更新完成。")

            # 2.2: 更新媒体主项目的演员列表
            logger.info(f"  -> 手动处理：步骤 2/2: 准备将 {len(cast_for_emby_handler)} 位演员更新到媒体主项目...")
            update_success = emby_handler.update_emby_item_cast(
                item_id=item_id, new_cast_list_for_handler=cast_for_emby_handler,
                emby_server_url=self.emby_url, emby_api_key=self.emby_api_key, user_id=self.emby_user_id
            )

            if not update_success:
                logger.error(f"  -> 手动处理失败：更新 Emby 项目 '{item_name}' 演员信息时失败。")
                with get_central_db_connection(self.db_path) as conn:
                    self.log_db_manager.save_to_failed_log(conn.cursor(), item_id, item_name, "手动API更新演员信息失败", item_type)
                return False

            # ======================================================================
            # ★★★ 新增功能 2: 如果是剧集，则批量处理所有分集 ★★★
            # ======================================================================
            if item_type == "Series":
                self._batch_update_episodes_cast(
                    series_id=item_id,
                    series_name=item_name,
                    final_cast_list=cast_for_emby_handler # 直接使用我们准备好的列表
                )

            # ======================================================================
            # 步骤 3: 完成上锁和刷新
            # ======================================================================
            logger.info("  -> 手动更新成功")
            fields_to_lock = ["Cast"] if self.auto_lock_cast_enabled else None
            emby_handler.refresh_emby_item_metadata(
                item_emby_id=item_id, emby_server_url=self.emby_url, emby_api_key=self.emby_api_key,
                user_id_for_ops=self.emby_user_id, lock_fields=fields_to_lock,
                replace_all_metadata_param=False, item_name_for_log=item_name
            )

            # ======================================================================
            # 步骤 4: 更新处理日志
            # ======================================================================
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                self.log_db_manager.save_to_processed_log(cursor, item_id, item_name, score=10.0)
                self.log_db_manager.remove_from_failed_log(cursor, item_id)

            logger.info(f"  -> 手动处理 '{item_name}' 流程完成。")
            return True

        except Exception as e:
            logger.error(f"  -> 手动处理 '{item_name}' 时发生严重错误: {e}", exc_info=True)
            return False
        finally:
            if item_id in self.manual_edit_cache:
                del self.manual_edit_cache[item_id]
                logger.trace(f"已清理 ItemID {item_id} 的手动编辑会话缓存。")
    # --- 为前端准备演员列表用于编辑 ---
    def get_cast_for_editing(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        【V-API-Optimized - 性能与展示优化最终版】
        1. 演员头像直接从本地数据库缓存的 TMDB 路径拼接，加载速度极快。
        2. 角色名在返回给前端前进行清理，去除“饰 ”等前缀。
        """
        logger.info(f"  -> 为编辑页面准备数据：ItemID {item_id}")
        
        try:
            # 步骤 1: 获取 Emby 基础详情 (保持不变)
            emby_details = emby_handler.get_emby_item_details(item_id, self.emby_url, self.emby_api_key, self.emby_user_id)
            if not emby_details:
                raise ValueError(f"在Emby中未找到项目 {item_id}")

            item_name_for_log = emby_details.get("Name", f"未知(ID:{item_id})")
            
            # 步骤 2: 获取演员列表 (保持不变)
            logger.debug(f"  -> 正在为 '{item_name_for_log}' 获取演员列表...")
            raw_emby_people = emby_details.get("People", [])
            full_cast_enhanced = self._enrich_cast_from_db_and_api(raw_emby_people)
            
            if not full_cast_enhanced:
                logger.warning(f"项目 '{item_name_for_log}' 没有演员信息失败。")

            # 步骤 3: 缓存完整数据 (保持不变)
            cast_for_cache = []
            for actor in full_cast_enhanced:
                actor_copy = actor.copy()
                actor_copy['id'] = actor.get("ProviderIds", {}).get("Tmdb")
                actor_copy['emby_person_id'] = actor.get("Id")
                actor_copy['name'] = actor.get("Name")
                actor_copy['character'] = actor.get("Role")
                cast_for_cache.append(actor_copy)
            self.manual_edit_cache[item_id] = cast_for_cache
            logger.debug(f"已为 ItemID {item_id} 缓存了 {len(cast_for_cache)} 条完整演员数据。")

            # ★★★★★★★★★★★★★★★ 步骤 4: 构建前端数据 (全新优化) ★★★★★★★★★★★★★★★
            cast_for_frontend = []
            
            # 为了从数据库获取头像路径，我们需要一个数据库连接
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                
                for actor_data in cast_for_cache:
                    tmdb_id = actor_data.get('id')
                    image_url = None
                    
                    # --- 核心优化 1: 从数据库获取头像路径 ---
                    if tmdb_id:
                        # 调用我们已有的辅助函数
                        actor_metadata = self._get_actor_metadata_from_cache(tmdb_id, cursor)
                        if actor_metadata and actor_metadata.get("profile_path"):
                            profile_path = actor_metadata["profile_path"]
                            # 拼接 TMDB 小尺寸头像 URL，加载速度飞快
                            image_url = f"https://image.tmdb.org/t/p/w185{profile_path}"
                    
                    # --- 核心优化 2: 清理角色名 ---
                    original_role = actor_data.get('character', '')
                    cleaned_role_for_display = utils.clean_character_name_static(original_role)

                    cast_for_frontend.append({
                        "tmdbId": tmdb_id,
                        "name": actor_data.get('name'),
                        "role": cleaned_role_for_display, # ★ 使用清理后的角色名
                        "imageUrl": image_url,             # ★ 使用拼接的 TMDB 头像 URL
                        "emby_person_id": actor_data.get('emby_person_id')
                    })
            
            # 步骤 5: 准备并返回最终的响应数据 (保持不变)
            failed_log_info = {}
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT error_message, score FROM failed_log WHERE item_id = ?", (item_id,))
                row = cursor.fetchone()
                if row: failed_log_info = dict(row)

            response_data = {
                "item_id": item_id,
                "item_name": emby_details.get("Name"),
                "item_type": emby_details.get("Type"),
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
            logger.error(f"  -> 获取编辑数据失败 for ItemID {item_id}: {e}", exc_info=True)
            return None
    
    # ★★★ 全量备份到覆盖缓存 ★★★
    def sync_all_media_assets(self, update_status_callback: Optional[callable] = None):
        """
        此版本通过比较Emby中的最后修改时间来智能判断是否需要更新备份，
        从而支持对连载中剧集的持续备份。
        """
        task_name = "覆盖缓存备份"
        logger.trace(f"--- 开始执行 '{task_name}' 任务 ---")

        if not self.local_data_path:
            logger.error(f"'{task_name}' 失败：未在配置中设置“本地数据源路径”。")
            if update_status_callback: update_status_callback(-1, "未配置本地数据源路径")
            return

        items_to_check = []
        try:
            with get_central_db_connection(self.db_path) as conn:
                cursor = conn.cursor()
                # ★★★ 核心修改 1：获取所有已处理的项目，以及它们上次备份时记录的修改时间 ★★★
                cursor.execute("SELECT item_id, item_name, last_emby_modified_at FROM processed_log")
                items_to_check = cursor.fetchall()
        except sqlite3.OperationalError as e:
            if "no such column: last_emby_modified_at" in str(e):
                error_msg = "数据库缺少 'last_emby_modified_at' 字段，请先更新表结构！"
                logger.error(error_msg)
                if update_status_callback: update_status_callback(-1, error_msg)
            else:
                logger.error(f"获取待检查项目列表时发生数据库错误: {e}", exc_info=True)
                if update_status_callback: update_status_callback(-1, "数据库错误")
            return
        except Exception as e:
            logger.error(f"获取待检查项目列表时发生未知错误: {e}", exc_info=True)
            if update_status_callback: update_status_callback(-1, "数据库未知错误")
            return

        total = len(items_to_check)
        if total == 0:
            logger.info("  -> 日志中没有任何项目，任务结束。")
            if update_status_callback: update_status_callback(100, "没有任何已处理的项目")
            return

        logger.info(f"  -> 将检查 {total} 个已处理的媒体项是否有更新。")
        
        stats = {"updated": 0, "skipped_no_change": 0, "skipped_other": 0, "cleaned": 0}

        with get_central_db_connection(self.db_path) as conn_sync:
            cursor_sync = conn_sync.cursor()
            for i, db_row in enumerate(items_to_check):
                if self.is_stop_requested():
                    logger.info(f"'{task_name}' 任务被中止。")
                    break

                item_id = db_row['item_id']
                item_name_from_db = db_row['item_name']
                last_known_modified_at = db_row['last_emby_modified_at']
                
                if update_status_callback:
                    update_status_callback(int((i / total) * 100), f"({i+1}/{total}): 正在检查 {item_name_from_db}")

                try:
                    # 步骤A: 获取项目最新的详细信息
                    item_details = emby_handler.get_emby_item_details(item_id, self.emby_url, self.emby_api_key, self.emby_user_id, fields="ProviderIds,Type,DateModified")
                    
                    if not item_details:
                        raise ValueError(f"项目在Emby中已不存在或无法访问 (ID: {item_id})")

                    current_emby_modified_at = item_details.get("DateModified")

                    # ★★★ 核心修改 2：比较修改时间，决定是否需要更新 ★★★
                    # 如果是首次备份(last_known_modified_at is None)，或者Emby中的修改时间更新了，则执行备份
                    if not last_known_modified_at or (current_emby_modified_at and current_emby_modified_at > last_known_modified_at):
                        logger.info(f"  -> 发现更新 '{item_name_from_db}'，准备执行备份...")
                        
                        tmdb_id = item_details.get("ProviderIds", {}).get("Tmdb")
                        item_type = item_details.get("Type")
                        if not tmdb_id:
                            logger.warning(f"跳过 '{item_name_from_db}'，因为它缺少 TMDb ID。")
                            stats["skipped_other"] += 1
                            continue

                        # --- 执行备份任务 ---
                        self.sync_item_images(item_details)
                        self.sync_item_metadata(item_details, tmdb_id) # 将元数据备份逻辑封装一下

                        # ★★★ 核心修改 3：更新数据库中的时间戳 ★★★
                        self.log_db_manager.mark_assets_as_synced(cursor_sync, item_id, current_emby_modified_at)
                        conn_sync.commit()
                        stats["updated"] += 1
                    else:
                        # 如果时间戳相同，说明无变化，直接跳过
                        logger.trace(f"'{item_name_from_db}' 未发生变化，跳过备份。")
                        stats["skipped_no_change"] += 1

                except ValueError as e:
                    logger.warning(f"项目 '{item_name_from_db}' (ID: {item_id}) 在 Emby 中已无法访问，将从日志中清理。")
                    self.log_db_manager.remove_from_processed_log(cursor_sync, item_id)
                    conn_sync.commit()
                    stats["cleaned"] += 1

                except Exception as e:
                    logger.error(f"处理项目 '{item_name_from_db}' (ID: {item_id}) 时发生未知错误: {e}", exc_info=True)
                    stats["skipped_other"] += 1
                
                time.sleep(0.1)

        logger.trace("--- 覆盖缓存备份任务结束 ---")
        final_message = f"✅ 更新: {stats['updated']}, 无变化跳过: {stats['skipped_no_change']}, 清理: {stats['cleaned']}, 其他跳过: {stats['skipped_other']}。"
        logger.info(final_message)
        if update_status_callback:
            update_status_callback(100, final_message)
   
    # --- 备份图片 ---
    def sync_item_images(self, item_details: Dict[str, Any], update_description: Optional[str] = None) -> bool:
        """
        【新增-重构】这个方法负责同步一个媒体项目的所有相关图片。
        它从 _process_item_core_logic 中提取出来，以便复用。
        """
        item_id = item_details.get("Id")
        item_type = item_details.get("Type")
        item_name_for_log = item_details.get("Name", f"未知项目(ID:{item_id})")
        
        if not all([item_id, item_type, self.local_data_path]):
            logger.error(f"  -> 跳过 '{item_name_for_log}'，因为缺少ID、类型或未配置本地数据路径。")
            return False

        try:
            # --- 准备工作 (目录、TMDb ID等) ---
            log_prefix = "图片备份："
            tmdb_id = item_details.get("ProviderIds", {}).get("Tmdb")
            if not tmdb_id:
                logger.warning(f"  -> {log_prefix} 项目 '{item_name_for_log}' 缺少TMDb ID，无法确定覆盖目录，跳过。")
                return False
            
            cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
            base_override_dir = os.path.join(self.local_data_path, "override", cache_folder_name, tmdb_id)
            image_override_dir = os.path.join(base_override_dir, "images")
            os.makedirs(image_override_dir, exist_ok=True)

            # --- 定义所有可能的图片映射 ---
            full_image_map = {"Primary": "poster.jpg", "Backdrop": "fanart.jpg", "Logo": "clearlogo.png"}
            if item_type == "Movie":
                full_image_map["Thumb"] = "landscape.jpg"

            # ★★★ 全新逻辑分发 ★★★
            images_to_sync = {}
            
            # 模式一：精准同步 (当描述存在时)
            if update_description:
                log_prefix = "[精准图片同步]"
                logger.debug(f"{log_prefix} 正在解析描述: '{update_description}'")
                
                # 定义关键词到Emby图片类型的映射 (使用小写以方便匹配)
                keyword_map = {
                    "primary": "Primary",
                    "backdrop": "Backdrop",
                    "logo": "Logo",
                    "thumb": "Thumb", # 电影缩略图
                    "banner": "Banner" # 剧集横幅 (如果需要可以添加)
                }
                
                desc_lower = update_description.lower()
                found_specific_image = False
                for keyword, image_type_api in keyword_map.items():
                    if keyword in desc_lower and image_type_api in full_image_map:
                        images_to_sync[image_type_api] = full_image_map[image_type_api]
                        logger.debug(f"{log_prefix} 匹配到关键词 '{keyword}'，将只同步 {image_type_api} 图片。")
                        found_specific_image = True
                        break # 找到第一个匹配就停止，避免重复
                
                if not found_specific_image:
                    logger.warning(f"{log_prefix} 未能在描述中找到可识别的图片关键词，将回退到完全同步。")
                    images_to_sync = full_image_map # 回退
            
            # 模式二：完全同步 (默认或回退)
            else:
                log_prefix = "图片备份:"
                logger.debug(f"  -> {log_prefix} 未提供更新描述，将同步所有类型的图片。")
                images_to_sync = full_image_map

            # --- 执行下载 ---
            logger.info(f"  -> {log_prefix} 开始为 '{item_name_for_log}' 下载 {len(images_to_sync)} 张图片至 {image_override_dir}...")
            for image_type, filename in images_to_sync.items():
                if self.is_stop_requested():
                    logger.warning(f"  -> {log_prefix} 收到停止信号，中止图片下载。")
                    return False
                emby_handler.download_emby_image(item_id, image_type, os.path.join(image_override_dir, filename), self.emby_url, self.emby_api_key)
            
            # --- 分集图片逻辑 (只有在完全同步时才考虑执行) ---
            if images_to_sync == full_image_map and item_type == "Series":
            
                children = emby_handler.get_series_children(item_id, self.emby_url, self.emby_api_key, self.emby_user_id, series_name_for_log=item_name_for_log) or []
                for child in children:
                    if self.is_stop_requested():
                        logger.warning(f"  -> {log_prefix} 收到停止信号，中止子项目图片下载。")
                        return False
                    child_type, child_id = child.get("Type"), child.get("Id")
                    if child_type == "Season":
                        season_number = child.get("IndexNumber")
                        if season_number is not None:
                            emby_handler.download_emby_image(child_id, "Primary", os.path.join(image_override_dir, f"season-{season_number}.jpg"), self.emby_url, self.emby_api_key)
                    elif child_type == "Episode":
                        season_number, episode_number = child.get("ParentIndexNumber"), child.get("IndexNumber")
                        if season_number is not None and episode_number is not None:
                            emby_handler.download_emby_image(child_id, "Primary", os.path.join(image_override_dir, f"season-{season_number}-episode-{episode_number}.jpg"), self.emby_url, self.emby_api_key)
            
            logger.info(f"  -> {log_prefix} ✅ 成功完成 '{item_name_for_log}' 的图片备份。")
            return True
        except Exception as e:
            logger.error(f"{log_prefix} 为 '{item_name_for_log}' 备份图片时发生未知错误: {e}", exc_info=True)
            return False
    
    # --- 备份元数据 ---
    def sync_item_metadata(self, item_details, tmdb_id):
        item_type = item_details.get("Type")
        cache_folder_name = "tmdb-movies2" if item_type == "Movie" else "tmdb-tv"
        source_cache_dir = os.path.join(self.local_data_path, "cache", cache_folder_name, tmdb_id)
        target_override_dir = os.path.join(self.local_data_path, "override", cache_folder_name, tmdb_id)
        if os.path.exists(source_cache_dir):
            os.makedirs(target_override_dir, exist_ok=True)
            shutil.copytree(source_cache_dir, target_override_dir, dirs_exist_ok=True)
            logger.info(f"    ✅ 成功将元数据从 '{source_cache_dir}' 备份到 '{target_override_dir}'。")
        else:
            logger.debug(f"    - 跳过元数据备份，因为源缓存目录不存在: {source_cache_dir}")

    def close(self):
        if self.douban_api: self.douban_api.close()

# actor_subscription_processor.py

import sqlite3
import json
import time
import re
from datetime import datetime, timedelta
import logging
from typing import Optional, Dict, Any, List, Set, Callable
import threading
from enum import Enum

import tmdb_handler
import emby_handler
from db_handler import get_db_connection
import moviepilot_handler

logger = logging.getLogger(__name__)

class MediaStatus(Enum):
    IN_LIBRARY = 'IN_LIBRARY'
    PENDING_RELEASE = 'PENDING_RELEASE'
    SUBSCRIBED = 'SUBSCRIBED'
    MISSING = 'MISSING'

class MediaType(Enum):
    MOVIE = 'Movie'
    SERIES = 'Series'

class ActorSubscriptionProcessor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.db_path = config.get('db_path')
        self.tmdb_api_key = config.get('tmdb_api_key')
        self.emby_url = config.get('emby_server_url')
        self.emby_api_key = config.get('emby_api_key')
        self.emby_user_id = config.get('emby_user_id')
        self.subscribe_delay_sec = config.get('subscribe_delay_sec', 0.5)
        self._stop_event = threading.Event()

    def signal_stop(self):
        self._stop_event.set()

    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def clear_stop_signal(self):
        self._stop_event.clear()

    def close(self):
        logger.trace("ActorSubscriptionProcessor closed.")

    def run_scheduled_task(self, update_status_callback: Optional[Callable] = None):
        def _update_status(progress, message):
            if update_status_callback:
                safe_progress = max(0, min(100, int(progress)))
                update_status_callback(safe_progress, message)

        logger.info("--- 开始执行定时演员订阅扫描任务 ---")
        _update_status(0, "正在准备订阅列表...")
        
        try:
            with get_db_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT id, actor_name FROM actor_subscriptions WHERE status = 'active'")
                subs_to_process = cursor.fetchall()
        except Exception as e:
            logger.error(f"定时任务：获取启用的订阅列表时失败: {e}", exc_info=True)
            _update_status(-1, "错误：获取订阅列表失败。")
            return
            
        if not subs_to_process:
            logger.info("定时任务：没有找到需要处理的演员订阅，任务结束。")
            _update_status(100, "没有需要处理的演员订阅。")
            return
            
        total_subs = len(subs_to_process)
        logger.info(f"定时任务：共找到 {total_subs} 个启用的订阅需要处理。")
        
        _update_status(5, "正在从 Emby 获取媒体库信息...")
        logger.info("任务开始，正在从 Emby 一次性获取全量媒体库数据...")
        emby_tmdb_ids: Set[str] = set()
        try:
            all_libraries = emby_handler.get_emby_libraries(self.emby_url, self.emby_api_key, self.emby_user_id)
            library_ids_to_scan = [lib['Id'] for lib in all_libraries if lib.get('CollectionType') in ['movies', 'tvshows']]
            emby_items = emby_handler.get_emby_library_items(base_url=self.emby_url, api_key=self.emby_api_key, user_id=self.emby_user_id, library_ids=library_ids_to_scan, media_type_filter="Movie,Series")
            
            if self.is_stop_requested():
                logger.info("任务在获取Emby媒体库后被用户中断。")
                return

            emby_tmdb_ids = {item['ProviderIds'].get('Tmdb') for item in emby_items if item.get('ProviderIds', {}).get('Tmdb')}
            logger.debug(f"已从 Emby 获取 {len(emby_tmdb_ids)} 个已入库媒体的 TMDb ID 用于后续对比。")
        except Exception as e:
            logger.error(f"定时任务：从 Emby 获取媒体库信息时发生严重错误: {e}", exc_info=True)
            _update_status(-1, "错误：连接 Emby 或获取数据失败。")
            return

        # ★★★ 核心修复 1：初始化一个用于本次任务全局的、记录已订阅媒体的集合 ★★★
        session_subscribed_ids: Set[str] = set()

        for i, sub in enumerate(subs_to_process):
            if self.is_stop_requested():
                logger.info("定时演员订阅扫描任务被用户中断。")
                break
            
            progress = int(5 + ((i + 1) / total_subs) * 95)
            message = f"({i+1}/{total_subs}) 正在扫描演员: {sub['actor_name']}"
            _update_status(progress, message)
            logger.info(message)
            
            # ★★★ 核心修复 2：将这个会话集合传递给每个演员的处理函数 ★★★
            self.run_full_scan_for_actor(sub['id'], emby_tmdb_ids, session_subscribed_ids)
            
            if not self.is_stop_requested() and i < total_subs - 1:
                time.sleep(1) 
                
        if not self.is_stop_requested():
            logger.info("--- 定时演员订阅扫描任务执行完毕 ---")
            _update_status(100, "所有订阅扫描完成。")


    def run_full_scan_for_actor(self, subscription_id: int, emby_tmdb_ids: Set[str], session_subscribed_ids: Optional[Set[str]] = None):
        """
        为单个订阅ID执行全量作品扫描。
        现在可以接受一个可选的 session_subscribed_ids 集合来防止重复订阅。
        """
        # 如果是手动触发单个扫描（session_subscribed_ids 未提供），则创建一个临时的空集合
        if session_subscribed_ids is None:
            session_subscribed_ids = set()

        logger.trace(f"--- 开始为订阅ID {subscription_id} 执行全量作品扫描 ---")
        try:
            with get_db_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                sub = cursor.execute("SELECT * FROM actor_subscriptions WHERE id = ?", (subscription_id,)).fetchone()
                if not sub: return
                
                logger.trace(f"正在处理演员: {sub['actor_name']} (TMDb ID: {sub['tmdb_person_id']})")

                old_tracked_media = self._get_existing_tracked_media(cursor, subscription_id)
                
                credits = tmdb_handler.get_person_credits_tmdb(sub['tmdb_person_id'], self.tmdb_api_key)
                if self.is_stop_requested() or not credits: return
                
                all_works = credits.get('movie_credits', {}).get('cast', []) + credits.get('tv_credits', {}).get('cast', [])
                logger.info(f"从TMDb获取到演员 {sub['actor_name']} 的 {len(all_works)} 部原始作品记录。")

                filtered_works = self._filter_works(all_works, sub)
                logger.info(f"根据规则筛选后，有 {len(filtered_works)} 部作品需要处理。")

                media_to_insert = []
                media_to_update = []
                today_str = datetime.now().strftime('%Y-%m-%d')

                for work in filtered_works:
                    if self.is_stop_requested(): break

                    media_id = work.get('id')
                    old_status = old_tracked_media.get(media_id)

                    # ★★★ 核心修复 3：将 session_subscribed_ids 进一步传递给状态判断函数 ★★★
                    current_status = self._determine_media_status(work, emby_tmdb_ids, today_str, old_status, session_subscribed_ids)
                    if not current_status: continue

                    if old_status is None:
                        media_to_insert.append(self._prepare_media_dict(work, subscription_id, current_status))
                    elif old_status != current_status.value:
                        media_to_update.append({'status': current_status.value, 'subscription_id': subscription_id, 'tmdb_media_id': media_id})
                    
                    old_tracked_media.pop(media_id, None)

                if self.is_stop_requested():
                    logger.info(f"任务在处理作品时被中断 (订阅ID: {subscription_id})。")
                    return

                media_ids_to_delete = list(old_tracked_media.keys())

                self._update_database_records(cursor, subscription_id, media_to_insert, media_to_update, media_ids_to_delete)
                
                conn.commit()
                logger.info(f"--- {sub['actor_name']} 的全量扫描成功完成 ---")

        except Exception as e:
            logger.error(f"为订阅ID {subscription_id} 执行扫描时发生严重错误: {e}", exc_info=True)

    def _get_existing_tracked_media(self, cursor: sqlite3.Cursor, subscription_id: int) -> Dict[int, str]:
        """从数据库获取当前已追踪的媒体及其状态。"""
        cursor.execute("SELECT tmdb_media_id, status FROM tracked_actor_media WHERE subscription_id = ?", (subscription_id,))
        return {row['tmdb_media_id']: row['status'] for row in cursor.fetchall()}

    def _filter_works(self, works: List[Dict], sub_config: sqlite3.Row) -> List[Dict]:
        """根据订阅配置过滤从TMDb获取的作品列表。"""
        filtered = []
        handled_media_ids = set()
        
        config_start_year = sub_config['config_start_year']
        
        raw_types_from_db = sub_config['config_media_types'].split(',')
        config_media_types = {
            'Series' if t.strip().lower() == 'tv' else t.strip().capitalize()
            for t in raw_types_from_db if t.strip()
        }

        config_genres_include = set(json.loads(sub_config['config_genres_include_json'] or '[]'))
        config_genres_exclude = set(json.loads(sub_config['config_genres_exclude_json'] or '[]'))
        config_min_rating = sub_config['config_min_rating']
        grace_period_months = 6
        six_months_ago = datetime.now() - timedelta(days=grace_period_months * 30)
        grace_period_end_date_str = six_months_ago.strftime('%Y-%m-%d')
        chinese_char_regex = re.compile(r'[\u4e00-\u9fff]')

        for work in works:
            media_id = work.get('id')
            if not media_id or media_id in handled_media_ids:
                continue
            
            release_date_str = work.get('release_date') or work.get('first_air_date', '')
            if not release_date_str: continue
            
            try:
                if int(release_date_str.split('-')[0]) < config_start_year: continue
            except (ValueError, IndexError): pass

            media_type_raw = work.get('media_type', 'movie' if 'title' in work else 'tv')
            media_type = MediaType.MOVIE.value if media_type_raw == 'movie' else MediaType.SERIES.value
            if media_type not in config_media_types:
                continue

            genre_ids = set(work.get('genre_ids', []))
            if config_genres_exclude and not genre_ids.isdisjoint(config_genres_exclude): continue
            if config_genres_include and genre_ids.isdisjoint(config_genres_include): continue

            if config_min_rating > 0:
                is_new_movie = release_date_str >= grace_period_end_date_str
                if not is_new_movie:
                    vote_average = work.get('vote_average', 0.0)
                    vote_count = work.get('vote_count', 0)
                    if vote_count > 50 and vote_average < config_min_rating:
                        logger.trace(f"过滤老片: '{work.get('title') or work.get('name')}' (评分 {vote_average} < {config_min_rating})")
                        continue
            
            title = work.get('title') or work.get('name', '')
            if not chinese_char_regex.search(title):
                logger.trace(f"过滤作品: '{title}' (排除无中文片名)。")
                continue
            
            handled_media_ids.add(media_id)
            filtered.append(work)
            
        return filtered

    def _determine_media_status(self, work: Dict, emby_tmdb_ids: Set[str], today_str: str, old_status: Optional[str], session_subscribed_ids: Set[str]) -> Optional[MediaStatus]:
        """
        判断单个作品的当前状态，如果需要则触发订阅。
        现在会检查会话级的已订阅列表以防止重复。
        """
        media_id_str = str(work.get('id'))
        release_date_str = work.get('release_date') or work.get('first_air_date', '')

        # 1. 最高优先级：检查是否已在 Emby 库中
        if media_id_str in emby_tmdb_ids:
            return MediaStatus.IN_LIBRARY
        
        # 2. 次高优先级：如果之前已经为【这个演员】订阅过，就保持订阅状态
        if old_status == MediaStatus.SUBSCRIBED.value:
            return MediaStatus.SUBSCRIBED

        # ★★★ 核心修复 4：在订阅前，检查是否已在【本次任务中】被其他演员订阅过 ★★★
        if media_id_str in session_subscribed_ids:
            logger.trace(f"作品 '{work.get('title') or work.get('name')}' (ID: {media_id_str}) 已在本次任务中被订阅，跳过重复请求。")
            return MediaStatus.SUBSCRIBED

        # 3. 检查是否是未来发行的作品
        if release_date_str > today_str:
            return MediaStatus.PENDING_RELEASE
        
        # 4. 最后，如果以上都不是，说明是已发行、不在库、且从未订阅过的作品，执行订阅
        logger.info(f"发现缺失作品: {work.get('title') or work.get('name')}，准备提交订阅...")
        success = False
        media_type_raw = work.get('media_type', 'movie' if 'title' in work else 'tv')

        if media_type_raw == 'movie':
            success = moviepilot_handler.subscribe_movie_to_moviepilot(
                movie_info={'title': work.get('title'), 'tmdb_id': work.get('id')}, config=self.config)
        else: # tv
            success = moviepilot_handler.subscribe_series_to_moviepilot(
                series_info={'item_name': work.get('name'), 'tmdb_id': work.get('id')}, season_number=None, config=self.config)
        
        time.sleep(self.subscribe_delay_sec)

        # ★★★ 核心修复 5：如果订阅成功，将会话ID添加到集合中，供后续演员检查 ★★★
        if success:
            session_subscribed_ids.add(media_id_str)
            return MediaStatus.SUBSCRIBED
        else:
            return MediaStatus.MISSING

    def _prepare_media_dict(self, work: Dict, subscription_id: int, status: MediaStatus) -> Dict:
        """根据作品信息和状态，准备用于插入数据库的字典。"""
        media_type_raw = work.get('media_type', 'movie' if 'title' in work else 'tv')
        media_type = MediaType.SERIES if media_type_raw == 'tv' else MediaType.MOVIE
        
        return {
            'subscription_id': subscription_id,
            'tmdb_media_id': work.get('id'),
            'media_type': media_type.value,
            'title': work.get('title') or work.get('name'),
            'release_date': work.get('release_date') or work.get('first_air_date', ''),
            'poster_path': work.get('poster_path'),
            'status': status.value,
            'emby_item_id': None
        }

    def _update_database_records(self, cursor: sqlite3.Cursor, subscription_id: int, to_insert: List[Dict], to_update: List[Dict], to_delete_ids: List[int]):
        """执行数据库的增、删、改操作。"""
        if to_insert:
            logger.info(f"新增 {len(to_insert)} 条作品记录。")
            cursor.executemany(
                "INSERT INTO tracked_actor_media (subscription_id, tmdb_media_id, media_type, title, release_date, poster_path, status, emby_item_id, last_updated_at) "
                "VALUES (:subscription_id, :tmdb_media_id, :media_type, :title, :release_date, :poster_path, :status, :emby_item_id, CURRENT_TIMESTAMP)",
                to_insert
            )
        
        if to_update:
            logger.info(f"更新 {len(to_update)} 条作品记录的状态。")
            cursor.executemany(
                "UPDATE tracked_actor_media SET status = :status, last_updated_at = CURRENT_TIMESTAMP "
                "WHERE subscription_id = :subscription_id AND tmdb_media_id = :tmdb_media_id",
                to_update
            )

        if to_delete_ids:
            logger.info(f"删除 {len(to_delete_ids)} 条过时的作品记录。")
            delete_params = [(subscription_id, media_id) for media_id in to_delete_ids]
            cursor.executemany(
                "DELETE FROM tracked_actor_media WHERE subscription_id = ? AND tmdb_media_id = ?",
                delete_params
            )
        
        cursor.execute("UPDATE actor_subscriptions SET last_checked_at = CURRENT_TIMESTAMP WHERE id = ?", (subscription_id,))
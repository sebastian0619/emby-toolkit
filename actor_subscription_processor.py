# actor_subscription_processor.py

import sqlite3
import json
import time
from datetime import datetime
import logging
from typing import Optional, Dict, Any, List, Set, Callable
import threading

import tmdb_handler
import emby_handler
from actor_utils import get_db_connection
import moviepilot_handler

logger = logging.getLogger(__name__)

class ActorSubscriptionProcessor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.db_path = config.get('db_path')
        self.tmdb_api_key = config.get('tmdb_api_key')
        self.emby_url = config.get('emby_server_url')
        self.emby_api_key = config.get('emby_api_key')
        self.emby_user_id = config.get('emby_user_id')
        self._stop_event = threading.Event()

    def signal_stop(self):
        self._stop_event.set()

    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def clear_stop_signal(self):
        self._stop_event.clear()

    def close(self): logger.trace("ActorSubscriptionProcessor closed.")

    def run_scheduled_task(self, update_status_callback: Optional[Callable] = None):
        
        def _update_status(progress, message):
            if update_status_callback:
                safe_progress = max(0, min(100, int(progress)))
                update_status_callback(safe_progress, message)

        logger.info("--- 开始执行定时演员订阅扫描任务 ---")
        _update_status(0, "正在准备订阅列表...")
        
        # ▼▼▼ 核心修改点 1：修改变量名，让它更清晰 ▼▼▼
        subs_to_process = []
        try:
            with get_db_connection(self.db_path) as conn:
                # 为了方便按列名访问，我们使用 Row 工厂
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                # ▼▼▼ 核心修改点 2：同时查询 id 和 actor_name ▼▼▼
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
        
        # ▼▼▼ 核心修改点 3：修改循环，同时处理 id 和 name ▼▼▼
        for i, sub in enumerate(subs_to_process):
            if self.is_stop_requested():
                logger.info("定时演员订阅扫描任务被用户中断。")
                break
            
            sub_id = sub['id']
            actor_name = sub['actor_name']
            
            progress = int(((i + 1) / total_subs) * 100)
            # ▼▼▼ 核心修改点 4：构造对用户友好的消息！▼▼▼
            message = f"({i+1}/{total_subs}) 正在扫描演员: {actor_name}"
            _update_status(progress, message)
            logger.info(message)
            
            # 传递给下一层函数的仍然是 ID
            self.run_full_scan_for_actor(sub_id)
            
            if not self.is_stop_requested():
                time.sleep(1) 
                
        if not self.is_stop_requested():
            logger.info("--- 定时演员订阅扫描任务执行完毕 ---")
            _update_status(100, "所有订阅扫描完成。")

    def run_full_scan_for_actor(self, subscription_id: int):
        logger.info(f"--- 开始为订阅ID {subscription_id} 执行全量作品扫描 ---")
        try:
            with get_db_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # ... (前面的代码不变，直到 all_works 赋值之后) ...
                cursor.execute("SELECT * FROM actor_subscriptions WHERE id = ?", (subscription_id,))
                sub = cursor.fetchone()
                if not sub: return
                tmdb_person_id = sub['tmdb_person_id']
                logger.info(f"正在处理演员: {sub['actor_name']} (TMDb ID: {tmdb_person_id})")
                all_libraries = emby_handler.get_emby_libraries(self.emby_url, self.emby_api_key, self.emby_user_id)
                library_ids_to_scan = [lib['Id'] for lib in all_libraries if lib.get('CollectionType') in ['movies', 'tvshows']]
                emby_items = emby_handler.get_emby_library_items(base_url=self.emby_url, api_key=self.emby_api_key, user_id=self.emby_user_id, library_ids=library_ids_to_scan, media_type_filter="Movie,Series")
                if self.is_stop_requested():
                    logger.info(f"任务在获取Emby媒体库后被中断 (订阅ID: {subscription_id})。")
                    return
                emby_tmdb_ids = {item['ProviderIds'].get('Tmdb') for item in emby_items if item.get('ProviderIds', {}).get('Tmdb')}
                logger.info(f"已获取 {len(emby_tmdb_ids)} 个已入库媒体的TMDb ID用于对比。")
                logger.info("正在清空旧的追踪记录，以确保数据总是最新的...")
                cursor.execute("DELETE FROM tracked_actor_media WHERE subscription_id = ?", (subscription_id,))
                credits = tmdb_handler.get_person_credits_tmdb(tmdb_person_id, self.tmdb_api_key)
                if self.is_stop_requested():
                    logger.info(f"任务在获取TMDb作品列表后被中断 (订阅ID: {subscription_id})。")
                    return
                if not credits: return
                all_works = credits.get('movie_credits', {}).get('cast', []) + credits.get('tv_credits', {}).get('cast', [])
                logger.info(f"从TMDb获取到演员 {sub['actor_name']} 的 {len(all_works)} 部原始作品记录。")

                processed_media = []
                today_str = datetime.now().strftime('%Y-%m-%d')
                config_start_year = sub['config_start_year']
                config_media_types = sub['config_media_types'].split(',')
                config_genres_include = set(json.loads(sub['config_genres_include_json'] or '[]'))
                config_genres_exclude = set(json.loads(sub['config_genres_exclude_json'] or '[]'))

                # ★★★ 核心修正：创建一个集合来跟踪本次扫描中已处理的作品ID ★★★
                handled_media_ids_this_scan = set()

                for work in all_works:
                    if self.is_stop_requested():
                        logger.info("演员作品扫描任务被用户中断。")
                        break

                    # ★★★ 核心修正：在循环开始时进行去重检查 ★★★
                    media_id = work.get('id')
                    # 如果没有ID，或者这个ID我们这次已经处理过了，就直接跳到下一个
                    if not media_id or media_id in handled_media_ids_this_scan:
                        continue
                    # 如果是新的，就把它记到我们的“临时清单”里
                    handled_media_ids_this_scan.add(media_id)

                    # ... (后面的所有过滤和订阅逻辑都保持不变) ...
                    release_date_str = work.get('release_date') or work.get('first_air_date', '')
                    if not release_date_str: continue
                    try:
                        release_year = int(release_date_str.split('-')[0])
                        if release_year < config_start_year: continue
                    except (ValueError, IndexError): pass
                    media_type_raw = work.get('media_type', 'movie' if 'title' in work else 'tv')
                    media_type = 'Movie' if media_type_raw == 'movie' else 'TV'
                    if media_type not in config_media_types: continue
                    genre_ids = set(work.get('genre_ids', []))
                    if config_genres_exclude and not genre_ids.isdisjoint(config_genres_exclude): continue
                    if config_genres_include and genre_ids.isdisjoint(config_genres_include): continue
                    media_id_str = str(media_id)
                    status = ''
                    if media_id_str in emby_tmdb_ids:
                        status = 'IN_LIBRARY'
                    else:
                        if release_date_str > today_str:
                            status = 'PENDING_RELEASE'
                        else:
                            logger.info(f"发现缺失作品: {work.get('title') or work.get('name')}，准备提交订阅...")
                            success = False
                            if media_type == 'Movie':
                                success = moviepilot_handler.subscribe_movie_to_moviepilot(movie_info={'title': work.get('title'), 'tmdb_id': work.get('id')}, config=self.config)
                            elif media_type == 'TV':
                                success = moviepilot_handler.subscribe_series_to_moviepilot(series_info={'item_name': work.get('name'), 'tmdb_id': work.get('id')}, season_number=None, config=self.config)
                            status = 'SUBSCRIBED' if success else 'MISSING'
                    if self.is_stop_requested():
                        logger.info("演员作品扫描任务在订阅后被用户中断。")
                        break
                    processed_media.append({'subscription_id': subscription_id, 'tmdb_media_id': work.get('id'), 'media_type': 'Series' if media_type == 'TV' else 'Movie', 'title': work.get('title') or work.get('name'), 'release_date': release_date_str, 'poster_path': work.get('poster_path'), 'status': status, 'emby_item_id': None})

                logger.info(f"筛选和处理后，共 {len(processed_media)} 条有效作品记录需要写入数据库。")
                if processed_media:
                    cursor.executemany("INSERT INTO tracked_actor_media (subscription_id, tmdb_media_id, media_type, title, release_date, poster_path, status, emby_item_id, last_updated_at) VALUES (:subscription_id, :tmdb_media_id, :media_type, :title, :release_date, :poster_path, :status, :emby_item_id, CURRENT_TIMESTAMP)", processed_media)
                
                cursor.execute("UPDATE actor_subscriptions SET last_checked_at = CURRENT_TIMESTAMP WHERE id = ?", (subscription_id,))
                conn.commit()
                logger.info(f"--- 订阅ID {subscription_id} 的全量扫描成功完成 ---")

        except Exception as e:
            logger.error(f"为订阅ID {subscription_id} 执行扫描时发生严重错误: {e}", exc_info=True)
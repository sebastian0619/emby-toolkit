# watchlist_processor.py

import sqlite3
import time
import json
import os
import copy
from typing import Optional, Dict, Any, List
from datetime import datetime
import threading

# 导入我们需要的辅助模块
from logger_setup import logger
import tmdb_handler
import emby_handler

class WatchlistProcessor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.db_path = config.get('db_path')
        if not self.db_path:
            raise ValueError("数据库路径 (db_path) 未在配置中提供。")
        
        self.tmdb_api_key = self.config.get("tmdb_api_key", "")
        self.emby_url = self.config.get("emby_server_url")
        self.emby_api_key = self.config.get("emby_api_key")
        self.emby_user_id = self.config.get("emby_user_id")
        self.local_data_path = self.config.get("local_data_path", "")
        self._stop_event = threading.Event()
        logger.debug("WatchlistProcessor 初始化完成。")

    def signal_stop(self): self._stop_event.set()
    def clear_stop_signal(self): self._stop_event.clear()
    def is_stop_requested(self) -> bool: return self._stop_event.is_set()
    def close(self): logger.debug("WatchlistProcessor closed.")

    def _get_db_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn
    
    # ★★★ 核心修复：确保这个方法在类的内部，有正确的缩进 ★★★
    def _read_local_json(self, file_path: str) -> Optional[Dict[str, Any]]:
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取本地JSON文件失败: {file_path}, 错误: {e}")
            return None

    def add_series_to_watchlist(self, item_details: Dict[str, Any]):
        """
        【V2 - 修复版】检查剧集状态，如果是“在播中”，则添加到追剧列表。
        这个方法将被 core_processor 调用。
        """
        item_type = item_details.get("Type")
        
        # 我们只关心剧集
        if item_type != "Series":
            return

        tmdb_id = item_details.get("ProviderIds", {}).get("Tmdb")
        item_name = item_details.get("Name")
        item_id = item_details.get("Id")

        if not tmdb_id:
            logger.debug(f"剧集 '{item_name}' 缺少 TMDb ID，无法进行自动追剧判断。")
            return

        # ★★★ 核心修复：在这里，我们必须去TMDb获取最权威的状态信息 ★★★
        logger.debug(f"正在从TMDb获取剧集 '{item_name}' 的最新状态以进行追剧判断...")
        if not self.tmdb_api_key:
            logger.warning("未配置TMDb API Key，无法进行自动追剧判断。")
            return
            
        # 调用TMDb API获取剧集详情，我们不需要附加信息，所以 append_to_response=None
        tmdb_details = tmdb_handler.get_tv_details_tmdb(tmdb_id, self.tmdb_api_key, append_to_response=None)

        if not tmdb_details:
            logger.warning(f"无法从TMDb获取 '{item_name}' 的详情，无法进行自动追剧判断。")
            return

        # ★★★ 现在，我们用TMDb返回的状态进行判断 ★★★
        tmdb_status = tmdb_details.get("status")
        
        if tmdb_status in ["Returning Series", "In Production", "Planned"]:
            if not all([item_id, tmdb_id, item_name]):
                logger.warning(f"尝试自动添加剧集到追剧列表失败：缺少关键ID或名称。")
                return

            try:
                with self._get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT OR IGNORE INTO watchlist (item_id, tmdb_id, item_name, item_type, status)
                        VALUES (?, ?, ?, ?, 'Watching')
                    """, (item_id, tmdb_id, item_name, item_type))
                    
                    if cursor.rowcount > 0:
                        logger.info(f"剧集 '{item_name}' (TMDb状态: {tmdb_status}) 已自动加入追剧列表。")
                    else:
                        logger.debug(f"剧集 '{item_name}' 已存在于追剧列表，无需重复添加。")
            except Exception as e:
                logger.error(f"自动添加剧集 '{item_name}' 到追剧列表时发生数据库错误: {e}", exc_info=True)
        else:
            logger.debug(f"剧集 '{item_name}' 的TMDb状态为 '{tmdb_status}'，不符合自动添加条件。")

    # ★★★ 核心修复：确保这个方法在 class WatchlistProcessor: 的内部，有正确的缩进 ★★★
    def process_watching_list(self, item_id: Optional[str] = None):
        """
        【V5 - 融合版】处理追剧列表。
        如果提供了 item_id，则只处理该项目。
        否则，处理所有状态为 'Watching' 的项目。
        """
        # 根据是否传入 item_id，调整日志和查询逻辑
        if item_id:
            logger.info(f"--- 开始执行单项追剧更新任务 (ItemID: {item_id}) ---")
        else:
            logger.info("--- 开始执行全量追剧列表更新任务 ---")
        
        watching_series = []
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                if item_id:
                    # 只查询指定的项目
                    cursor.execute("SELECT * FROM watchlist WHERE item_id = ?", (item_id,))
                else:
                    # 查询所有 'Watching' 的项目
                    cursor.execute("SELECT * FROM watchlist WHERE status = 'Watching'")
                
                watching_series = [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"获取追剧列表时发生数据库错误: {e}")
            return

        if not watching_series:
            if item_id:
                logger.warning(f"未在追剧列表中找到项目 {item_id}，任务中止。")
            else:
                logger.info("追剧列表中没有需要检查的剧集。")
            return

        if not item_id:
             logger.info(f"发现 {len(watching_series)} 部剧集需要检查更新...")

        # 后续的循环逻辑完全保持不变，因为它天然支持处理一个或多个项目
        for series in watching_series:
            if self.is_stop_requested():
                logger.info("追剧列表更新任务被中止。")
                break

            item_id = series['item_id']
            tmdb_id = series['tmdb_id']
            item_name = series['item_name']
            
            logger.info(f"正在检查剧集: '{item_name}' (TMDb ID: {tmdb_id})")

            if not self.tmdb_api_key:
                logger.warning("未配置TMDb API Key，跳过。")
                continue
            
            override_dir = os.path.join(self.local_data_path, "override", "tmdb-tv", tmdb_id)
            os.makedirs(override_dir, exist_ok=True)
            cache_dir = os.path.join(self.local_data_path, "cache", "tmdb-tv", tmdb_id)
            
            base_series_data = tmdb_handler.get_tv_details_tmdb(tmdb_id, self.tmdb_api_key, append_to_response=None)
            if not base_series_data:
                logger.warning(f"无法从TMDb获取 '{item_name}' 的基础详情，跳过本次更新。")
                continue

            new_status = base_series_data.get("status")
            if new_status in ["Ended", "Canceled"]:
                logger.info(f"剧集 '{item_name}' 的状态已变为 '{new_status}'，将自动更新追剧列表状态。")
                with self._get_db_connection() as conn:
                    conn.execute("UPDATE watchlist SET status = 'Ended' WHERE item_id = ?", (item_id,))
                continue

            cache_series_json = self._read_local_json(os.path.join(cache_dir, "series.json")) or {}
            actor_cast_data = cache_series_json.get("credits", {}).get("cast", [])
            
            final_series_data = base_series_data
            final_series_data.setdefault("credits", {})["cast"] = actor_cast_data
            try:
                with open(os.path.join(override_dir, "series.json"), 'w', encoding='utf-8') as f:
                    json.dump(final_series_data, f, ensure_ascii=False, indent=4)
                logger.info(f"  已为 '{item_name}' 更新总的 series.json。")
            except Exception as e:
                logger.error(f"  写入 series.json 时失败: {e}")
                continue

            number_of_seasons = base_series_data.get("number_of_seasons", 0)
            logger.info(f"'{item_name}' 共有 {number_of_seasons} 季，开始生成独立的季/集元数据文件...")
            
            for season_num in range(0, number_of_seasons + 1):
                season_details = tmdb_handler.get_season_details_tmdb(tmdb_id, season_num, self.tmdb_api_key)
                if not season_details:
                    logger.warning(f"  获取第 {season_num} 季的详情失败，跳过。")
                    continue
                
                season_details.setdefault("credits", {})["cast"] = actor_cast_data
                
                try:
                    with open(os.path.join(override_dir, f"season-{season_num}.json"), 'w', encoding='utf-8') as f:
                        json.dump(season_details, f, ensure_ascii=False, indent=4)
                    logger.debug(f"    已生成 season-{season_num}.json。")
                except Exception as e:
                    logger.error(f"    写入 season-{season_num}.json 时失败: {e}")

                for episode_details in season_details.get("episodes", []):
                    episode_num = episode_details.get("episode_number")
                    if episode_num is None: continue
                    
                    episode_details.setdefault("credits", {})["cast"] = actor_cast_data
                    
                    try:
                        with open(os.path.join(override_dir, f"season-{season_num}-episode-{episode_num}.json"), 'w', encoding='utf-8') as f:
                            json.dump(episode_details, f, ensure_ascii=False, indent=4)
                    except Exception as e:
                        logger.error(f"    写入 season-{season_num}-episode-{episode_num}.json 时失败: {e}")

                time.sleep(0.2)

            try:
                with self._get_db_connection() as conn:
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    conn.execute("UPDATE watchlist SET last_checked_at = ? WHERE item_id = ?", (current_time, item_id))
            except Exception as e:
                logger.error(f"更新 '{item_name}' 的last_checked_at时间戳时失败: {e}")

            emby_handler.refresh_emby_item_metadata(item_id, self.emby_url, self.emby_api_key, item_name_for_log=item_name)

            time.sleep(1)

        logger.info("--- 追剧列表更新任务结束 ---")
    # ★★★ 新增：处理单个追剧项目的方法 ★★★
    def process_single_watching_item(self, item_id: str):
        """
        只处理追剧列表中的一个特定项目。
        """
        logger.info(f"--- 开始执行单项追剧更新任务 (ItemID: {item_id}) ---")
        
        series_info = None
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()
                # 从数据库中只获取这一个项目的信息
                cursor.execute("SELECT * FROM watchlist WHERE item_id = ?", (item_id,))
                row = cursor.fetchone()
                if row:
                    series_info = dict(row)
        except Exception as e:
            logger.error(f"获取项目 {item_id} 的追剧信息时发生数据库错误: {e}")
            return

        if not series_info:
            logger.warning(f"未在追剧列表中找到项目 {item_id}，任务中止。")
            return

        # --- 后续的逻辑和 process_watching_list 中的循环体完全一样 ---
        tmdb_id = series_info['tmdb_id']
        item_name = series_info['item_name']
        
        logger.info(f"正在检查剧集: '{item_name}' (TMDb ID: {tmdb_id})")

        if not self.tmdb_api_key:
            logger.warning("未配置TMDb API Key，跳过。")
            return
        
        # ... (后面所有获取TMDb数据、合并、写入、刷新的逻辑，
        #      都可以从 process_watching_list 的 for 循环内部复制过来) ...
        # ...
        # 为了简洁，我把这部分逻辑直接写出来

        # 1. 获取剧集基础信息
        base_series_data = tmdb_handler.get_tv_details_tmdb(tmdb_id, self.tmdb_api_key, append_to_response=None)
        if not base_series_data:
            logger.warning(f"无法从TMDb获取 '{item_name}' 的基础详情，跳过本次更新。")
            return

        # 2. 检查剧集是否已完结
        new_status = base_series_data.get("status")
        if new_status in ["Ended", "Canceled"]:
            logger.info(f"剧集 '{item_name}' 的状态已变为 '{new_status}'，将自动更新追剧列表状态。")
            with self._get_db_connection() as conn:
                conn.execute("UPDATE watchlist SET status = 'Ended' WHERE item_id = ?", (item_id,))
            return

        # 3. 准备目录和演员表数据
        override_dir = os.path.join(self.local_data_path, "override", "tmdb-tv", tmdb_id)
        os.makedirs(override_dir, exist_ok=True)
        cache_dir = os.path.join(self.local_data_path, "cache", "tmdb-tv", tmdb_id)
        
        cache_series_json = self._read_local_json(os.path.join(cache_dir, "series.json")) or {}
        actor_cast_data = cache_series_json.get("credits", {}).get("cast", [])

        # 4. 逐季获取详情并生成所有文件
        number_of_seasons = base_series_data.get("number_of_seasons", 0)
        all_seasons_details_for_series_json = []
        
        for season_num in range(0, number_of_seasons + 1):
            # ... (这整个循环和里面的文件写入逻辑，和 process_watching_list 里的一样) ...
            season_details = tmdb_handler.get_season_details_tmdb(tmdb_id, season_num, self.tmdb_api_key)
            if not season_details: continue
            all_seasons_details_for_series_json.append(season_details.copy())
            season_details.setdefault("credits", {})["cast"] = actor_cast_data
            with open(os.path.join(override_dir, f"season-{season_num}.json"), 'w', encoding='utf-8') as f:
                json.dump(season_details, f, ensure_ascii=False, indent=4)
            for episode_details in season_details.get("episodes", []):
                episode_num = episode_details.get("episode_number")
                if episode_num is None: continue
                episode_details.setdefault("credits", {})["cast"] = actor_cast_data
                with open(os.path.join(override_dir, f"season-{season_num}-episode-{episode_num}.json"), 'w', encoding='utf-8') as f:
                    json.dump(episode_details, f, ensure_ascii=False, indent=4)
            time.sleep(0.2)

        # 5. 更新总的 series.json
        final_series_data = base_series_data
        final_series_data['seasons'] = all_seasons_details_for_series_json
        final_series_data.setdefault("credits", {})["cast"] = actor_cast_data
        with open(os.path.join(override_dir, "series.json"), 'w', encoding='utf-8') as f:
            json.dump(final_series_data, f, ensure_ascii=False, indent=4)
        logger.info(f"  已为 '{item_name}' 更新所有元数据文件。")

        # 6. 更新时间戳和刷新Emby
        with self._get_db_connection() as conn:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("UPDATE watchlist SET last_checked_at = ? WHERE item_id = ?", (current_time, item_id))
        
        emby_handler.refresh_emby_item_metadata(item_id, self.emby_url, self.emby_api_key, item_name_for_log=item_name)
        
        logger.info(f"--- 单项追剧更新任务完成 (ItemID: {item_id}) ---")
    

